"""EventGroupProcessor coordinator — drives the per-group processing pipeline.

Fetch → filter → match → team-filter → channels → XMLTV, plus the batch loop
over all groups and post-processing enforcement. The heavy lifting lives in
the sibling mixin modules; this module owns orchestration and shared state.
"""

import logging
import time
from collections.abc import Callable
from datetime import date
from sqlite3 import Connection
from typing import Any

from teamarr.consumers.channel_lifecycle import (
    StreamProcessResult,
    create_lifecycle_service,
)
from teamarr.consumers.enforcement import (
    CrossGroupEnforcer,
    KeywordEnforcer,
    KeywordOrderingEnforcer,
)
from teamarr.consumers.event_epg import EventEPGGenerator
from teamarr.core import Event
from teamarr.database.groups import (
    EventEPGGroup,
    get_all_group_xmltv,
    get_all_groups,
    get_enabled_soccer_leagues,
    update_group_stats,
)
from teamarr.database.settings import get_feed_separation_settings
from teamarr.database.subscription import (
    get_subscription_template_for_event,
    get_subscription_templates,
)
from teamarr.services import SportsDataService, create_default_service
from teamarr.utilities.art_url import read_art_base_url
from teamarr.utilities.tz import now_utc
from teamarr.utilities.xmltv import merge_xmltv_content

from .matching import StreamMatching, _separation_applies
from .persistence import MatchPersistence
from .preview import PreviewBuilder
from .results import (
    BatchProcessingResult,
    EnforcementStepResult,
    PreviewResult,
    ProcessingResult,
)
from .stream_fetcher import StreamFetcher
from .team_filter import TeamFiltering
from .xmltv import XmltvRenderer

logger = logging.getLogger(__name__)


class EventGroupProcessor(
    StreamFetcher,
    StreamMatching,
    TeamFiltering,
    MatchPersistence,
    XmltvRenderer,
    PreviewBuilder,
):
    """Processes event groups - matches streams to events and manages channels.

    Usage:
        from teamarr.database import get_db
        from teamarr.dispatcharr import get_factory

        factory = get_factory(get_db)
        client = factory.get_client()

        processor = EventGroupProcessor(
            db_factory=get_db,
            dispatcharr_client=client,
        )

        # Process all active groups (there is no single-group run; #645)
        result = processor.process_all_groups()
    """

    def __init__(
        self,
        db_factory: Any,
        dispatcharr_client: Any = None,
        service: SportsDataService | None = None,
    ):
        """Initialize the processor.

        Args:
            db_factory: Factory function returning database connection
            dispatcharr_client: Optional DispatcharrClient for Dispatcharr operations
            service: Optional SportsDataService (creates default if not provided)
        """
        self._db_factory = db_factory
        self._dispatcharr_client = dispatcharr_client
        self._service = service or create_default_service()

        # EPG generator for XMLTV output (art_base_url injected so the resolver
        # reconstructs game-thumbs URLs — epic z02s).

        self._art_base_url = read_art_base_url(db_factory)
        self._epg_generator = EventEPGGenerator(self._service, art_base_url=self._art_base_url)

        # Shared events cache for cross-group reuse in a single generation run
        # Keys are "league:date" strings, values are (events, was_cache_only) tuples
        # was_cache_only=True means the result came from a cache-only lookup (no API call attempted)
        # This avoids redundant API/cache lookups when multiple groups search the same leagues
        # while ensuring groups that need fresh API data can still get it
        self._shared_events: dict[str, tuple[list[Event], bool]] = {}

        # Per-run consolidation state (this processor lives for one run):
        # one lifecycle service shared by all groups, and once-per-run flags for
        # work that used to repeat per group (scheduled deletions, the initial
        # channel-number reassign pass).
        self._lifecycle_service = None
        self._deletions_processed = False
        self._initial_reassign_done = False

    def _get_lifecycle_service(self):
        """Lifecycle service shared across all groups in this run.

        The Dispatcharr channel cache is class-level (shared per URL), so
        per-group construction never bought cache freshness — it only repeated
        settings reads and the external-occupied computation ~160 times per run.
        """
        if self._lifecycle_service is None:
            self._lifecycle_service = create_lifecycle_service(
                self._db_factory,
                self._service,
                self._dispatcharr_client,
            )
            # Compute external channel numbers to avoid collisions (#146)
            self._lifecycle_service.compute_external_occupied()
        return self._lifecycle_service

    def _resolve_subscription_leagues(
        self, conn: Connection, group: "EventEPGGroup | None" = None
    ) -> list[str]:
        """Resolve leagues from subscription with per-group override support.

        Priority chain (follows _get_effective_team_filter pattern):
        1. Group's own subscription overrides (if configured)
        2. Global sports subscription (default)

        Handles soccer_mode resolution (all/teams/manual) from whichever
        level provides the subscription.

        Args:
            conn: Database connection
            group: Optional group to check for overrides

        Returns:
            List of league codes with soccer leagues expanded based
            on the effective soccer_mode.
        """
        from teamarr.database.subscription import get_subscription

        # Determine effective subscription source
        if group and group.subscription_leagues is not None:
            # Group has its own subscription override
            base_leagues = list(group.subscription_leagues)
            soccer_mode = group.subscription_soccer_mode
            soccer_followed_teams = group.subscription_soccer_followed_teams
        else:
            # Fall back to global subscription
            sub = get_subscription(conn)
            base_leagues = list(sub.leagues) if sub.leagues else []
            soccer_mode = sub.soccer_mode
            soccer_followed_teams = sub.soccer_followed_teams

        if soccer_mode == "all":
            # Replace any manually-selected soccer leagues with ALL enabled
            soccer_leagues = get_enabled_soccer_leagues(conn)
            non_soccer = [lg for lg in base_leagues if lg not in soccer_leagues]
            return non_soccer + soccer_leagues

        if soccer_mode == "teams" and soccer_followed_teams:
            from teamarr.consumers.cache.queries import TeamLeagueCache

            cache = TeamLeagueCache(self._db_factory)
            discovered: set[str] = set()
            for team in soccer_followed_teams:
                provider = team.get("provider", "espn")
                team_id = team.get("team_id")
                if team_id:
                    team_leagues = cache.get_team_leagues(team_id, provider, sport="soccer")
                    discovered.update(team_leagues)
            return list(set(base_leagues) | discovered)

        # 'manual' or NULL: use subscription leagues as-is
        return base_leagues

    def _get_subscription_leagues(
        self, conn: Connection, group: "EventEPGGroup | None" = None
    ) -> list[str]:
        """Get subscription leagues, cached per group for the current run.

        Groups with no overrides share the global cache (key=0).
        Groups with overrides get their own cached result (key=group.id).
        """
        if not hasattr(self, "_subscription_leagues_cache"):
            self._subscription_leagues_cache: dict[int, list[str]] = {}

        # Key: 0 for global, group.id for overridden groups
        has_override = group is not None and group.subscription_leagues is not None
        cache_key = group.id if group is not None and has_override else 0

        if cache_key not in self._subscription_leagues_cache:
            self._subscription_leagues_cache[cache_key] = self._resolve_subscription_leagues(
                conn, group
            )
        return self._subscription_leagues_cache[cache_key]

    def process_all_groups(
        self,
        target_date: date | None = None,
        run_enforcement: bool = True,
        progress_callback: Callable[[int, int, str], None] | None = None,
        generation: int | None = None,
        aggregate_xmltv: bool = True,
        run_id: int | None = None,
    ) -> BatchProcessingResult:
        """Process all active event groups.

        All groups are processed equally in sort_order. No parent/child
        distinction — every group creates channels and generates XMLTV.
        Leagues come from the global sports subscription.

        After all groups, enforcement runs to fix any misplaced streams.

        Args:
            target_date: Target date (defaults to today)
            run_enforcement: Whether to run post-processing enforcement
            progress_callback: Optional callback(current, total, group_name)
            generation: Cache generation counter (shared across all groups)
            aggregate_xmltv: Whether to populate ``result.total_xmltv`` by
                merging every group's guide. Full EPG generation passes False:
                it re-reads team AND group XMLTV from the database and merges
                those itself, so merging the group half here parses and
                serializes the whole guide a second time for a value it
                discards.
            run_id: processing_runs id of the parent full_epg run. Per-stream
                match details are keyed on it; when None (tests, ad-hoc
                callers) no details are persisted. Groups do not get their
                own run rows (#645) — their counters roll up into the parent.

        Returns:
            BatchProcessingResult with all group results and combined XMLTV
        """
        target_date = target_date or date.today()
        batch_result = BatchProcessingResult()
        self._generation = generation  # Store for use in _do_matching

        # Clear caches at start of new generation run
        self._shared_events.clear()
        self.clear_epg_resolution_cache()
        if hasattr(self, "_subscription_leagues_cache"):
            del self._subscription_leagues_cache
        self._lifecycle_service = None
        self._deletions_processed = False
        self._initial_reassign_done = False

        with self._db_factory() as conn:
            # Sync the system-managed "Dispatcharr Channels" source group (183.9) to
            # the global setting before loading groups. When enabled it joins the
            # normal processing loop; when disabled it stays out and its channels are
            # reaped by the disabled-group cleanup. (EPG matching is always available;
            # only the channel-source toggle gates this system group.)
            try:
                from teamarr.database.groups import ensure_channel_source_group

                _cs_row = conn.execute(
                    "SELECT epg_channel_source_enabled FROM settings WHERE id = 1"
                ).fetchone()
                _channel_source_on = bool(_cs_row and _cs_row["epg_channel_source_enabled"])
                ensure_channel_source_group(conn, _channel_source_on)
            except Exception as e:
                logger.warning("[CHANNEL_SOURCE] Failed to sync source group: %s", e)

            groups = get_all_groups(conn, include_disabled=False)
            total_groups = len(groups)
            processed_count = 0

            if progress_callback:
                if total_groups > 0:
                    progress_callback(
                        0,
                        total_groups,
                        f"Found {total_groups} groups to process",
                    )
                else:
                    progress_callback(0, 1, "No event groups configured")

            processed_group_ids = []

            for group in groups:
                if progress_callback:
                    progress_callback(
                        processed_count,
                        total_groups,
                        f"Loading {group.name}...",
                    )

                stream_cb = None
                if progress_callback:

                    def make_stream_cb(grp_name: str, grp_idx: int):
                        def cb(
                            current: int,
                            total: int,
                            stream_name: str,
                            matched: bool,
                        ):
                            icon = "✓" if matched else "✗"
                            msg = f"{icon} {current}/{total} — {grp_name}: {stream_name}"
                            progress_callback(grp_idx, total_groups, msg)

                        return cb

                    stream_cb = make_stream_cb(group.name, processed_count + 1)

                status_cb = None
                if progress_callback:
                    grp_idx = processed_count + 1

                    def make_status_cb(grp_name: str, idx: int):
                        def cb(msg: str):
                            progress_callback(idx, total_groups, f"{grp_name}: {msg}")

                        return cb

                    status_cb = make_status_cb(group.name, grp_idx)

                result = self._process_group_internal(
                    conn,
                    group,
                    target_date,
                    stream_progress_callback=stream_cb,
                    status_callback=status_cb,
                    run_id=run_id,
                )
                batch_result.results.append(result)
                processed_group_ids.append(group.id)
                processed_count += 1
                if progress_callback:
                    stats = f"({result.streams_matched}/{result.streams_fetched} matched)"
                    progress_callback(
                        processed_count,
                        total_groups,
                        f"{group.name} {stats}",
                    )

            # Aggregate XMLTV from all processed groups
            if aggregate_xmltv and processed_group_ids:
                xmltv_contents = get_all_group_xmltv(conn, processed_group_ids)
                if xmltv_contents:
                    from teamarr.database.settings import get_display_settings

                    display_settings = get_display_settings(conn)
                    batch_result.total_xmltv = merge_xmltv_content(
                        xmltv_contents,
                        generator_name=display_settings.xmltv_generator_name,
                        generator_url=display_settings.xmltv_generator_url,
                    )
                    logger.info(
                        f"Aggregated XMLTV from {len(xmltv_contents)} groups"
                        f", {len(batch_result.total_xmltv)} bytes"
                    )

        # Enforcement runs AFTER the connection above has closed, and must keep
        # doing so (#607). Every enforcer opens its own connection, while this
        # method's connection writes (store_group_xmltv, per group) and does not
        # commit until its `with` exits — so running them inside that block put
        # two connections from one thread on either side of the SQLite write
        # lock. The enforcer would wait out the full 30s busy_timeout and lose
        # its write, every run, because the lock could not be released until the
        # block it was blocking exited.
        #
        # Closing first also means the enforcers read the run they are enforcing:
        # a second connection sees only committed state, so while the groups
        # phase was mid-transaction they were reading the database as it looked
        # BEFORE this run.
        #
        # Nothing here feeds the XMLTV aggregation above — enforcement moves
        # streams and deletes channels, it never rewrites a group's stored guide
        # — so the reordering is safe.
        if run_enforcement:
            enforcement_lifecycle = None
            if self._dispatcharr_client:
                enforcement_lifecycle = self._get_lifecycle_service()
            batch_result.enforcement = self._run_enforcement(
                [g.id for g in groups],
                lifecycle_service=enforcement_lifecycle,
            )

        batch_result.completed_at = now_utc()
        return batch_result

    def _run_enforcement(
        self,
        multi_league_ids: list[int],
        lifecycle_service=None,
    ) -> list[EnforcementStepResult]:
        """Run post-processing enforcement.

        V1 Parity: Runs every EPG generation:
        1. Keyword enforcement: ensure streams are on correct keyword channels
        2. Cross-group consolidation: merge multi-league into single-league
        3. Keyword ordering: ensure main channel < keyword channels in numbering
        4. Orphan cleanup: delete Dispatcharr channels not tracked in DB
        5. Disabled group cleanup: delete channels from disabled groups
        6. Subscription cleanup: delete channels for unsubscribed leagues

        Each step is isolated: a failure is recorded on that step's result and
        the remaining steps still run. The per-step outcomes are returned (and
        surfaced in run stats) instead of vanishing into warning logs.

        Takes no connection on purpose (#607): every step opens its own, and
        this must only ever be called once the caller's connection has closed.
        See the call site in process_all_groups.

        Args:
            multi_league_ids: IDs of multi-league groups for cross-group check
            lifecycle_service: Optional lifecycle service for orphan/disabled cleanup

        Returns:
            One EnforcementStepResult per executed step.
        """
        channel_manager = self._dispatcharr_client.channels if self._dispatcharr_client else None
        steps: list[EnforcementStepResult] = []

        def run_step(name: str, runner: Callable[[], int]) -> None:
            step = EnforcementStepResult(step=name)
            try:
                step.count = runner()
            except Exception as e:
                step.ok = False
                step.error = str(e)
                logger.warning("[EVENT_EPG] Enforcement step '%s' failed: %s", name, e)
            steps.append(step)

        # 1. Keyword enforcement: move streams to correct keyword channels
        def keyword_step() -> int:
            keyword_enforcer = KeywordEnforcer(self._db_factory, channel_manager)
            moved = keyword_enforcer.enforce().moved_count
            if moved > 0:
                logger.info("[EVENT_EPG] Keyword enforcement moved %d streams", moved)
            return moved

        run_step("keyword", keyword_step)

        # 2. Cross-group consolidation (only if multi-league groups exist)
        if multi_league_ids:

            def cross_group_step() -> int:
                cross_group_enforcer = CrossGroupEnforcer(self._db_factory, channel_manager)
                deleted = cross_group_enforcer.enforce(multi_league_ids).deleted_count
                if deleted > 0:
                    logger.info(f"Cross-group consolidation: {deleted} channels merged")
                return deleted

            run_step("cross_group", cross_group_step)

        # 3. Keyword ordering: ensure main channel has lower number than keyword channels
        def ordering_step() -> int:
            ordering_enforcer = KeywordOrderingEnforcer(self._db_factory, channel_manager)
            reordered = ordering_enforcer.enforce().reordered_count
            if reordered > 0:
                logger.info(f"Keyword ordering: reordered {reordered} channel pair(s)")
            return reordered

        run_step("ordering", ordering_step)

        if lifecycle_service:
            # 4. Orphan cleanup: delete Dispatcharr channels not tracked in DB
            def orphan_step() -> int:
                deleted = lifecycle_service.cleanup_orphan_dispatcharr_channels().get("deleted", 0)
                if deleted > 0:
                    logger.info(f"Orphan cleanup: deleted {deleted} Dispatcharr channels")
                return deleted

            run_step("orphan_cleanup", orphan_step)

            # 5. Disabled group cleanup: delete channels from disabled groups
            def disabled_step() -> int:
                deleted = lifecycle_service.cleanup_disabled_groups().get("deleted") or []
                if deleted:
                    logger.info(f"Disabled group cleanup: deleted {len(deleted)} channels")
                return len(deleted)

            run_step("disabled_groups", disabled_step)

            # 6. Subscription cleanup: delete channels for leagues no longer subscribed.
            #    Followed leagues/teams can change while the source group stays enabled;
            #    the stream sync won't remove those streams (they're still in the M3U),
            #    so the channels linger until a full wipe without this (psoi).
            def subscription_step() -> int:
                with self._db_factory() as conn:
                    return self._cleanup_unsubscribed_leagues(conn, lifecycle_service)

            run_step("subscription_cleanup", subscription_step)

        return steps

    def _cleanup_unsubscribed_leagues(self, conn: Connection, lifecycle_service) -> int:
        """Delete channels whose league is no longer subscribed in any enabled group.

        The "Subscriptions" (followed leagues/teams) can change between runs while
        the source group stays enabled. The group keeps matching its M3U streams, so
        the per-group stream sync never removes them — leaving stale channels for
        dropped leagues until a full wipe. This sweeps them immediately (psoi).

        Conservative by construction:
        - Union of effective subscribed leagues across all enabled, non-system
          groups (reusing _resolve_subscription_leagues, so soccer expansion etc.
          are honored).
        - Bails out if that union is empty (never mass-delete on a resolution miss).
        - Skips channel-source/system groups and channels with no league.

        Returns:
            Number of channels deleted.
        """
        from teamarr.database.channels import get_all_managed_channels

        all_groups = get_all_groups(conn, include_disabled=True)
        enabled_real_groups = [
            g for g in all_groups if g.enabled and not getattr(g, "is_channel_source", False)
        ]
        subscribed: set[str] = set()
        for g in enabled_real_groups:
            subscribed.update(lg.lower() for lg in self._resolve_subscription_leagues(conn, g))

        # Safety guard: an empty set means we couldn't resolve any subscription —
        # never interpret that as "delete everything".
        if not subscribed:
            return 0

        # Channels owned by channel-source/system groups are exempt (their leagues
        # aren't driven by the subscription).
        system_group_ids = {g.id for g in all_groups if getattr(g, "is_channel_source", False)}

        deleted = 0
        for ch in get_all_managed_channels(conn, include_deleted=False):
            league = (ch.league or "").strip().lower()
            if not league or league in subscribed:
                continue
            if ch.event_epg_group_id in system_group_ids:
                continue
            if lifecycle_service.delete_managed_channel(conn, ch.id, reason="unsubscribed_league"):
                deleted += 1

        if deleted:
            logger.info(
                "[EVENT_EPG] Subscription cleanup: deleted %d channel(s) for unsubscribed leagues",
                deleted,
            )
        return deleted

    def _process_group_internal(
        self,
        conn: Connection,
        group: EventEPGGroup,
        target_date: date,
        stream_progress_callback: Callable | None = None,
        status_callback: Callable[[str], None] | None = None,
        run_id: int | None = None,
    ) -> ProcessingResult:
        """Internal processing for a single group.

        Args:
            conn: Database connection
            group: Event group to process
            target_date: Target date for matching
            stream_progress_callback: Optional callback(current, total, stream_name, matched)
            status_callback: Optional callback(status_message) for phase updates
            run_id: Parent full_epg run id for match-detail persistence (None = skip)
        """
        result = ProcessingResult(group_id=group.id, group_name=group.name)

        # Template is required — check subscription templates
        sub_templates = get_subscription_templates(conn)
        has_template = len(sub_templates) > 0

        if not has_template:
            logger.warning(
                "[EVENT_GROUP_SKIP] Group '%s' (id=%d): no template assigned - "
                "template is required for channel naming. Skipping group.",
                group.name,
                group.id,
            )
            result.errors.append("No template assigned - template is required for channel naming")
            result.completed_at = now_utc()
            return result

        # Per-phase wall time for this group, reported on the parent run's
        # per-group breakdown so slow groups can be diagnosed without log
        # archaeology.
        phases = result.phase_timings
        _phase_start = time.time()

        def _mark(phase: str) -> None:
            nonlocal _phase_start
            now = time.time()
            phases[phase] = round(phases.get(phase, 0.0) + (now - _phase_start), 2)
            _phase_start = now

        try:
            # Clear any previously stored XMLTV for this group so that if
            # processing crashes or produces zero matches, stale rendered
            # output is never served in the merged EPG.
            self._store_group_xmltv(conn, group.id, "")

            # Step 1: Fetch M3U streams from Dispatcharr
            streams = self._fetch_streams(group)
            result.streams_fetched = len(streams)
            _mark("fetch")

            if not streams:
                result.errors.append("No streams found for group")
                result.completed_at = now_utc()
                return result

            # Step 1.5: Apply stream filtering (include/exclude regex)
            streams, filter_result = self._filter_streams(streams, group)
            result.streams_after_filter = filter_result.passed_count
            result.filtered_stale = filter_result.filtered_stale
            # Combine all built-in eligibility filters into filtered_not_event
            # (placeholder, unsupported_sport, and not_event are all controlled by skip_builtin)
            result.filtered_not_event = (
                filter_result.filtered_not_event
                + filter_result.filtered_placeholder
                + filter_result.filtered_unsupported_sport
            )
            result.filtered_include_regex = filter_result.filtered_include
            result.filtered_exclude_regex = filter_result.filtered_exclude

            if not streams:
                result.errors.append("All streams filtered out by regex patterns")
                result.completed_at = now_utc()
                # Still update stats even if all filtered
                update_group_stats(
                    conn,
                    group.id,
                    stream_count=0,
                    matched_count=0,
                    filtered_stale=filter_result.filtered_stale,
                    filtered_include_regex=filter_result.filtered_include,
                    filtered_exclude_regex=filter_result.filtered_exclude,
                    filtered_not_event=filter_result.filtered_not_event,
                    total_stream_count=result.streams_fetched,  # V1 parity
                )
                return result

            # Step 2: Resolve subscription leagues (per-group override → global
            # fallback). The matcher fetches its own events with a 30-day window,
            # so there is no separate pre-flight event fetch here — an eventless
            # group simply produces zero matches downstream.
            effective_leagues = self._get_subscription_leagues(conn, group)

            # Step 3: Match streams to events (uses fingerprint cache)
            match_result = self._match_streams(
                streams,
                group,
                target_date,
                stream_progress_callback=stream_progress_callback,
                status_callback=status_callback,
                resolved_leagues=effective_leagues,
            )
            _mark("match")
            # Coverage = distinct streams; volume = total matched results (EPG/TEAM_ONLY
            # fan one stream out to many results, which is why the old result-count
            # numerator pushed match rate over 100%).
            result.streams_matched = match_result.matched_stream_count
            result.streams_unmatched = match_result.unmatched_stream_count
            result.match_result_count = match_result.matched_count
            result.streams_cached = match_result.cache_hits

            # Count matcher-level exclusions (matched but excluded by league/event_final)
            for r in match_result.results:
                if r.matched and not r.included and r.exclusion_reason:
                    result.streams_excluded += 1
                    if r.exclusion_reason == "event_final":
                        result.excluded_event_final += 1
                    elif r.exclusion_reason.startswith("league_not_included"):
                        result.excluded_league_not_included += 1

            # Save detailed match results for analysis, keyed on the parent run
            if run_id is not None:
                self._save_match_details(
                    conn=conn,
                    run_id=run_id,
                    group_id=group.id,
                    group_name=group.name,
                    streams=streams,
                    match_result=match_result,
                )

            # Step 4: Create/update channels
            matched_streams = self._build_matched_stream_list(
                streams, match_result, stream_timezone=group.stream_timezone
            )

            # Step 4a: Resolve feed hints to actual teams. Always runs (#527):
            # identification persists per-stream for team_feed ordering rules;
            # feed_separation.enabled gates only channel splitting.
            feed_settings = get_feed_separation_settings(conn)
            matched_streams = self._resolve_feed_teams(
                matched_streams,
                feed_settings.detect_team_names,
                feed_settings.enabled,
                feed_settings.sports,
            )

            # Sort channels: sport → league → time → event_id (fixed order since v59)
            matched_streams = self._sort_matched_streams(matched_streams)

            # Enrich ALL matched events with fresh status from provider
            # This ensures lifecycle filtering uses current final status
            matched_streams = self._enrich_matched_events(matched_streams)
            _mark("enrich")

            # Build event lookup BEFORE team filtering (for cleanup of existing channels)
            # Use segment-aware event_id to match channel.event_id storage
            def _effective_event_id(m) -> str | None:
                event = m.get("event")
                if not event or not hasattr(event, "id"):
                    return None
                segment = m.get("segment")
                return f"{event.id}-{segment}" if segment else event.id

            all_matched_events: dict[str, Event] = {
                eid: ev
                for m in matched_streams
                if (eid := _effective_event_id(m)) and (ev := m.get("event"))
            }

            # Apply team include/exclude filtering
            matched_streams, filtered_team_count = self._filter_by_teams(
                matched_streams, group, conn
            )
            result.filtered_team = filtered_team_count

            # Build set of event IDs that passed the filter (segment-aware)
            passed_event_ids = {eid for m in matched_streams if (eid := _effective_event_id(m))}

            # Cleanup existing channels that no longer pass team filter
            # (handles both include and exclude modes, global and per-group)
            cleanup_count = self._cleanup_team_filtered_channels(
                group, conn, all_matched_events, passed_event_ids
            )
            if cleanup_count > 0:
                result.channels_deleted = cleanup_count
                logger.info("[EVENT_EPG] Cleaned up %d channels due to team filter", cleanup_count)

            # Reclaim feed-separated channels that lost eligibility — the master
            # toggle went off (#672) or their sport left feed_separation.sports
            # (#732). Runs BEFORE channel processing so the freed streams re-land
            # on the base channel in this same pass instead of duplicating it for
            # a day. Events still eligible are exempt, so this is a no-op on the
            # steady-state run rather than something gated on the toggle.
            separated_event_ids = (
                {
                    eid
                    for m in matched_streams
                    if (eid := _effective_event_id(m))
                    and _separation_applies(m.get("event"), feed_settings.sports)
                }
                if feed_settings.enabled
                else set()
            )
            feed_cleanup_count = self._cleanup_feed_separated_channels(
                group, conn, passed_event_ids, separated_event_ids
            )
            if feed_cleanup_count > 0:
                result.channels_deleted += feed_cleanup_count
                logger.info(
                    "[EVENT_EPG] Reclaimed %d feed-separated channel(s) — "
                    "feed separation no longer applies",
                    feed_cleanup_count,
                )

            # Build stream dict for cleanup (fingerprint-based content change detection)
            current_streams = {sid: s for s in streams if (sid := s.get("id"))}

            if matched_streams:
                if status_callback:
                    status_callback(f"Processing {len(matched_streams)} channels...")
                lifecycle_result = self._process_channels(
                    matched_streams, group, conn, current_streams=current_streams
                )
                _mark("channels")
                result.channels_created = len(lifecycle_result.created)
                result.channels_existing = len(lifecycle_result.existing)
                result.channels_skipped = len(lifecycle_result.skipped)
                # += : the team-filter and feed-separation sweeps above already
                # counted their deletions into this field.
                result.channels_deleted += len(lifecycle_result.deleted)
                result.channel_errors = len(lifecycle_result.errors)
                # Add lifecycle exclusions to total
                result.streams_excluded += len(lifecycle_result.excluded)

                # Compute excluded breakdown by reason (lifecycle exclusions)
                for excl in lifecycle_result.excluded:
                    reason = excl.get("reason", "")
                    if reason == "event_final":
                        result.excluded_event_final += 1
                    elif reason == "event_past":
                        result.excluded_event_past += 1
                    elif reason == "before_window":
                        result.excluded_before_window += 1
                    elif reason == "league_not_included":
                        result.excluded_league_not_included += 1


                for error in lifecycle_result.errors:
                    result.errors.append(f"Channel error: {error}")

                # Step 5: Generate XMLTV from matched streams
                # Filter out streams excluded by lifecycle (event_final, event_past, etc.)
                excluded_event_ids = {
                    excl.get("event_id")
                    for excl in lifecycle_result.excluded
                    if excl.get("event_id")
                }
                xmltv_streams = [
                    ms
                    for ms in matched_streams
                    if ms.get("event") and ms["event"].id not in excluded_event_ids
                ]

                if status_callback:
                    status_callback(f"Generating EPG for {len(xmltv_streams)} events...")
                xmltv_content, programmes_total, event_programmes, pregame, postgame = (
                    self._generate_xmltv(xmltv_streams, group, conn)
                )
                result.programmes_generated = programmes_total
                result.events_count = event_programmes
                result.pregame_count = pregame
                result.postgame_count = postgame
                result.xmltv_size = len(xmltv_content.encode("utf-8")) if xmltv_content else 0

                # Step 6: Store XMLTV for this group (in database)
                # Always store, even if empty - this clears stale XMLTV when no events match
                self._store_group_xmltv(conn, group.id, xmltv_content or "")
                _mark("xmltv")

            # Update group's processing stats
            update_group_stats(
                conn,
                group.id,
                stream_count=result.streams_after_filter,
                matched_count=result.streams_matched,
                match_result_count=result.match_result_count,
                filtered_stale=result.filtered_stale,
                filtered_include_regex=result.filtered_include_regex,
                filtered_exclude_regex=result.filtered_exclude_regex,
                failed_count=result.streams_unmatched,
                filtered_not_event=result.filtered_not_event,
                filtered_team=result.filtered_team,
                streams_excluded=result.streams_excluded,
                total_stream_count=result.streams_fetched,  # V1 parity
                excluded_event_final=result.excluded_event_final,
                excluded_event_past=result.excluded_event_past,
                excluded_before_window=result.excluded_before_window,
                excluded_league_not_included=result.excluded_league_not_included,
            )

        except Exception as e:
            logger.exception(f"Error processing group {group.name}")
            result.errors.append(str(e))

        result.completed_at = now_utc()
        return result

    def _process_channels(
        self,
        matched_streams: list[dict],
        group: EventEPGGroup,
        conn: Connection,
        current_streams: dict[int, dict] | None = None,
    ) -> StreamProcessResult:
        """Create/update channels via ChannelLifecycleService.

        V1 Parity: Full lifecycle management with every generation:
        1. Process scheduled deletions (expired channels)
        2. Cleanup deleted/changed streams (missing from M3U or content changed)
        3. Create/update channels
        4. Sync existing channel settings
        5. Reassign channel numbers if needed

        Args:
            matched_streams: List of matched stream dicts with event data
            group: Event EPG group
            conn: Database connection
            current_streams: Dict mapping stream_id -> stream_data for cleanup
        """
        from teamarr.consumers.lifecycle import StreamProcessResult

        lifecycle_service = self._get_lifecycle_service()

        # Build group config dict
        # Per-group profiles/channel groups removed — now resolved from
        # per-league subscription config → global defaults
        group_config = {
            "id": group.id,
            "m3u_account_id": group.m3u_account_id,
            "m3u_account_name": group.m3u_account_name,
        }

        # Load template from database if configured
        # Resolve template from global subscription
        # Typed Any: _load_event_template yields an EventTemplateConfig, which the
        # lifecycle creator accepts even though its param is annotated ``dict | None``.
        template_config: Any = None
        template_id = get_subscription_template_for_event(conn, "", "")
        if template_id:
            template_config = self._load_event_template(conn, template_id)

        combined_result = StreamProcessResult()

        from teamarr.database.channel_numbers import (
            is_sticky_mode,
            reassign_all_channels,
        )

        # v59: Global channel reassignment before processing
        # Ensures all channels have correct numbers based on global mode.
        # Runs once per run (not per group — each group's post-pass already
        # left numbers correct for the next group). Skipped in sticky
        # (gap/strict) modes — those defer all placement to the single
        # end-of-run pass in generation, which is the only one that pushes
        # to Dispatcharr (see is_sticky_mode).
        if not self._initial_reassign_done:
            self._initial_reassign_done = True
            try:
                with lifecycle_service._db_factory() as conn:
                    if not is_sticky_mode(conn):
                        reassign_result = reassign_all_channels(
                            conn, external_occupied=lifecycle_service._external_occupied
                        )
                        if reassign_result.get("channels_moved"):
                            logger.info(
                                "[EVENT_EPG] Pre-process reassignment: %d channels moved",
                                reassign_result["channels_moved"],
                            )
            except Exception as e:
                logger.debug("[EVENT_EPG] Error in global reassignment: %s", e)

        # V1 Parity Step 1: Process scheduled deletions first
        # Once per run — expired channels don't reappear between groups, so the
        # per-group repetition was ~160 identical sweeps per generation.
        if not self._deletions_processed:
            self._deletions_processed = True
            try:
                deletion_result = lifecycle_service.process_scheduled_deletions()
                combined_result.merge(deletion_result)
                if deletion_result.deleted:
                    logger.info(
                        "[EVENT_EPG] Deleted %d expired channels", len(deletion_result.deleted)
                    )
            except Exception as e:
                logger.debug("[EVENT_EPG] Error processing scheduled deletions: %s", e)

        # V1 Parity Step 2: Cleanup deleted/missing/changed streams
        if current_streams is not None:
            try:
                cleanup_result = lifecycle_service.cleanup_deleted_streams(
                    group.id,
                    current_streams,
                    matched_streams=matched_streams,
                    is_channel_source=bool(getattr(group, "is_channel_source", False)),
                )
                combined_result.merge(cleanup_result)
                if cleanup_result.deleted:
                    deleted_count = len(cleanup_result.deleted)
                    logger.info(f"Deleted {deleted_count} channels with missing/changed streams")
            except Exception as e:
                logger.debug("[EVENT_EPG] Error cleaning up deleted streams: %s", e)

        # V1 Parity Step 3-4: Create new channels and sync existing settings
        process_result = lifecycle_service.process_matched_streams(
            matched_streams, group_config, template_config
        )
        combined_result.merge(process_result)

        # v59: Post-process global reassignment (skipped in sticky modes — see
        # above). Only needed when this group actually changed the channel set;
        # a no-op group leaves numbering exactly as the last pass did.
        if combined_result.created or combined_result.deleted:
            try:
                with lifecycle_service._db_factory() as conn:
                    if not is_sticky_mode(conn):
                        reassign_result = reassign_all_channels(
                            conn, external_occupied=lifecycle_service._external_occupied
                        )
                        if reassign_result.get("channels_moved"):
                            logger.info(
                                "[EVENT_EPG] Post-process reassignment: %d channels moved",
                                reassign_result["channels_moved"],
                            )
            except Exception as e:
                logger.debug("[EVENT_EPG] Error reassigning channel numbers: %s", e)

        return combined_result


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


def process_all_event_groups(
    db_factory: Any,
    dispatcharr_client: Any = None,
    target_date: date | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    generation: int | None = None,
    service: SportsDataService | None = None,
    aggregate_xmltv: bool = True,
    run_id: int | None = None,
) -> BatchProcessingResult:
    """Process all active event groups.

    Convenience function that creates a processor and runs it.

    Args:
        db_factory: Factory function returning database connection
        dispatcharr_client: Optional DispatcharrClient
        target_date: Target date (defaults to today)
        progress_callback: Optional callback(current, total, group_name)
        generation: Cache generation counter (shared across all groups in run)
        service: Optional SportsDataService (reuse to maintain cache warmth)
        aggregate_xmltv: Populate result.total_xmltv (see
            EventGroupProcessor.process_all_groups)
        run_id: Parent full_epg run id (see EventGroupProcessor.process_all_groups)

    Returns:
        BatchProcessingResult
    """
    processor = EventGroupProcessor(
        db_factory=db_factory,
        dispatcharr_client=dispatcharr_client,
        service=service,
    )
    return processor.process_all_groups(
        target_date,
        progress_callback=progress_callback,
        generation=generation,
        aggregate_xmltv=aggregate_xmltv,
        run_id=run_id,
    )


def preview_event_group(
    db_factory: Any,
    group_id: int,
    dispatcharr_client: Any = None,
    target_date: date | None = None,
) -> PreviewResult:
    """Preview stream matching for an event group.

    Convenience function that creates a processor and previews.
    Does NOT create channels or generate EPG - only matches streams.

    Args:
        db_factory: Factory function returning database connection
        group_id: Group ID to preview
        dispatcharr_client: Optional DispatcharrClient
        target_date: Target date (defaults to today)

    Returns:
        PreviewResult with stream matching details
    """
    processor = EventGroupProcessor(
        db_factory=db_factory,
        dispatcharr_client=dispatcharr_client,
    )
    return processor.preview_group(group_id, target_date)
