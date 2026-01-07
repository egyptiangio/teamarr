"""Event Group Processor - orchestrates the full event-based EPG flow.

Connects stream matching to channel lifecycle:
1. Load group config from database
2. Fetch M3U streams from Dispatcharr
3. Fetch events from data providers (parallel with ThreadPoolExecutor)
4. Match streams to events
5. Create/update channels via ChannelLifecycleService
6. Generate XMLTV EPG
7. Optionally push EPG to Dispatcharr

This is the main entry point for event-based EPG generation.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from sqlite3 import Connection
from typing import Any, Callable

from teamarr.consumers.channel_lifecycle import (
    StreamProcessResult,
    create_lifecycle_service,
)
from teamarr.consumers.child_processor import ChildStreamProcessor
from teamarr.consumers.enforcement import (
    CrossGroupEnforcer,
    KeywordEnforcer,
    KeywordOrderingEnforcer,
)
from teamarr.consumers.event_epg import EventEPGGenerator, EventEPGOptions
from teamarr.consumers.filler.event_filler import (
    EventFillerConfig,
    EventFillerGenerator,
    EventFillerOptions,
    EventFillerResult,
    template_to_event_filler_config,
)
from teamarr.consumers.matching import BatchMatchResult, StreamMatcher
from teamarr.core import Event
from teamarr.database.groups import (
    EventEPGGroup,
    get_all_group_xmltv,
    get_all_groups,
    get_group,
    update_group_stats,
)
from teamarr.database.stats import (
    FailedMatch,
    MatchedStream,
    create_run,
    save_failed_matches,
    save_matched_streams,
    save_run,
)
from teamarr.services import SportsDataService, create_default_service
from teamarr.services.stream_filter import FilterResult
from teamarr.utilities.xmltv import merge_xmltv_content, programmes_to_xmltv

logger = logging.getLogger(__name__)

# Number of parallel workers for event fetching
MAX_WORKERS = 100


@dataclass
class ProcessingResult:
    """Result of processing an event group."""

    group_id: int
    group_name: str
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None

    # Stream fetching and filtering
    streams_fetched: int = 0
    streams_after_filter: int = 0  # After all filtering
    filtered_not_event: int = 0  # Didn't look like an event (no vs/@/at/date)
    filtered_include_regex: int = 0  # Didn't match include pattern
    filtered_exclude_regex: int = 0  # Matched exclude pattern

    # Stream matching
    streams_matched: int = 0
    streams_unmatched: int = 0
    streams_excluded: int = 0  # Matched but excluded by timing (past/final/early)

    # Excluded breakdown by reason
    excluded_event_final: int = 0
    excluded_event_past: int = 0
    excluded_before_window: int = 0
    excluded_league_not_included: int = 0

    # Channel lifecycle
    channels_created: int = 0
    channels_existing: int = 0
    channels_skipped: int = 0
    channels_deleted: int = 0
    channel_errors: int = 0

    # EPG generation
    programmes_generated: int = 0
    events_count: int = 0  # Actual event programmes (excluding filler)
    pregame_count: int = 0  # Pregame filler programmes
    postgame_count: int = 0  # Postgame filler programmes
    xmltv_size: int = 0

    # Errors
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "group_id": self.group_id,
            "group_name": self.group_name,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "streams": {
                "fetched": self.streams_fetched,
                "after_filter": self.streams_after_filter,
                "filtered_not_event": self.filtered_not_event,
                "filtered_include": self.filtered_include_regex,
                "filtered_exclude": self.filtered_exclude_regex,
                "matched": self.streams_matched,
                "unmatched": self.streams_unmatched,
            },
            "channels": {
                "created": self.channels_created,
                "existing": self.channels_existing,
                "skipped": self.channels_skipped,
                "deleted": self.channels_deleted,
                "errors": self.channel_errors,
            },
            "epg": {
                "programmes": self.programmes_generated,
                "events": self.events_count,
                "pregame": self.pregame_count,
                "postgame": self.postgame_count,
                "xmltv_bytes": self.xmltv_size,
            },
            "errors": self.errors,
        }


@dataclass
class BatchProcessingResult:
    """Result of processing multiple groups."""

    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    results: list[ProcessingResult] = field(default_factory=list)
    total_xmltv: str = ""

    @property
    def groups_processed(self) -> int:
        return len(self.results)

    @property
    def total_channels_created(self) -> int:
        return sum(r.channels_created for r in self.results)

    @property
    def total_errors(self) -> int:
        return sum(len(r.errors) for r in self.results)

    @property
    def total_programmes(self) -> int:
        return sum(r.programmes_generated for r in self.results)

    @property
    def total_events(self) -> int:
        """Actual event programmes (excluding filler)."""
        return sum(r.events_count for r in self.results)

    @property
    def total_pregame(self) -> int:
        """Total pregame filler programmes."""
        return sum(r.pregame_count for r in self.results)

    @property
    def total_postgame(self) -> int:
        """Total postgame filler programmes."""
        return sum(r.postgame_count for r in self.results)

    @property
    def total_streams_fetched(self) -> int:
        """Total streams fetched across all groups."""
        return sum(r.streams_fetched for r in self.results)

    @property
    def total_streams_matched(self) -> int:
        """Total streams matched across all groups."""
        return sum(r.streams_matched for r in self.results)

    @property
    def total_streams_unmatched(self) -> int:
        """Total streams unmatched across all groups."""
        return sum(r.streams_unmatched for r in self.results)

    @property
    def total_channels_deleted(self) -> int:
        """Total channels deleted across all groups."""
        return sum(r.channels_deleted for r in self.results)

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "groups_processed": self.groups_processed,
            "total_channels_created": self.total_channels_created,
            "total_errors": self.total_errors,
            "results": [r.to_dict() for r in self.results],
        }


@dataclass
class PreviewStream:
    """Individual stream preview result."""

    stream_id: int
    stream_name: str
    matched: bool
    event_id: str | None = None
    event_name: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    league: str | None = None
    start_time: str | None = None
    from_cache: bool = False
    exclusion_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "stream_id": self.stream_id,
            "stream_name": self.stream_name,
            "matched": self.matched,
            "event_id": self.event_id,
            "event_name": self.event_name,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "league": self.league,
            "start_time": self.start_time,
            "from_cache": self.from_cache,
            "exclusion_reason": self.exclusion_reason,
        }


@dataclass
class PreviewResult:
    """Result of previewing stream matches for a group."""

    group_id: int
    group_name: str

    # Totals
    total_streams: int = 0
    filtered_count: int = 0
    matched_count: int = 0
    unmatched_count: int = 0

    # Filter breakdown
    filtered_not_event: int = 0
    filtered_include_regex: int = 0
    filtered_exclude_regex: int = 0

    # Cache stats
    cache_hits: int = 0
    cache_misses: int = 0

    # Stream details
    streams: list[PreviewStream] = field(default_factory=list)

    # Errors
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "group_name": self.group_name,
            "total_streams": self.total_streams,
            "filtered_count": self.filtered_count,
            "matched_count": self.matched_count,
            "unmatched_count": self.unmatched_count,
            "filtered_not_event": self.filtered_not_event,
            "filtered_include_regex": self.filtered_include_regex,
            "filtered_exclude_regex": self.filtered_exclude_regex,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "streams": [s.to_dict() for s in self.streams],
            "errors": self.errors,
        }


class EventGroupProcessor:
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

        # Process a single group
        result = processor.process_group(group_id=1)

        # Process all active groups
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

        # EPG generator for XMLTV output
        self._epg_generator = EventEPGGenerator(self._service)

    def process_group(
        self,
        group_id: int,
        target_date: date | None = None,
    ) -> ProcessingResult:
        """Process a single event group.

        Args:
            group_id: Group ID to process
            target_date: Target date (defaults to today)

        Returns:
            ProcessingResult with all details
        """
        target_date = target_date or date.today()

        with self._db_factory() as conn:
            group = get_group(conn, group_id)
            if not group:
                result = ProcessingResult(group_id=group_id, group_name="Unknown")
                result.errors.append(f"Group {group_id} not found")
                result.completed_at = datetime.now()
                return result

            return self._process_group_internal(conn, group, target_date)

    def preview_group(
        self,
        group_id: int,
        target_date: date | None = None,
    ) -> PreviewResult:
        """Preview stream matching for a group without creating channels.

        Performs all matching logic but skips channel creation and EPG generation.
        Used for testing and previewing before actual processing.

        Args:
            group_id: Group ID to preview
            target_date: Target date (defaults to today)

        Returns:
            PreviewResult with stream matching details
        """
        target_date = target_date or date.today()

        with self._db_factory() as conn:
            group = get_group(conn, group_id)
            if not group:
                result = PreviewResult(group_id=group_id, group_name="Unknown")
                result.errors.append(f"Group {group_id} not found")
                return result

            result = PreviewResult(group_id=group_id, group_name=group.name)

            # Step 0: Refresh M3U account before fetching streams (skip if recent)
            if not self._dispatcharr_client:
                result.errors.append("Dispatcharr not configured")
                return result

            if group.m3u_account_id:
                try:
                    refresh_result = self._dispatcharr_client.m3u.wait_for_refresh(
                        group.m3u_account_id,
                        timeout=180,
                        skip_if_recent_minutes=60,
                    )
                    if refresh_result.skipped:
                        logger.debug(
                            f"Preview: M3U account {group.m3u_account_id} "
                            "recently refreshed, skipping"
                        )
                    elif refresh_result.success:
                        logger.debug(
                            f"Preview: M3U account {group.m3u_account_id} "
                            f"refreshed in {refresh_result.duration:.1f}s"
                        )
                    else:
                        logger.warning(
                            f"Preview: M3U refresh failed: {refresh_result.message} "
                            "- continuing with potentially stale data"
                        )
                except Exception as e:
                    logger.warning(f"Preview: M3U refresh error: {e} - continuing anyway")

            # Step 1: Fetch streams from M3U group
            try:
                raw_streams = self._dispatcharr_client.m3u.list_streams(
                    group_id=group.m3u_group_id,
                    account_id=group.m3u_account_id,
                )
            except Exception as e:
                result.errors.append(f"Failed to fetch streams: {e}")
                return result

            if not raw_streams:
                result.errors.append("No streams found in M3U group")
                return result

            # Convert DispatcharrStream objects to dict format
            streams = [{"id": s.id, "name": s.name} for s in raw_streams]
            result.total_streams = len(streams)

            # Step 2: Apply stream filtering
            streams, filter_result = self._filter_streams(streams, group)
            result.filtered_count = result.total_streams - filter_result.passed_count
            result.filtered_not_event = filter_result.filtered_not_event
            result.filtered_include_regex = filter_result.filtered_include
            result.filtered_exclude_regex = filter_result.filtered_exclude

            if not streams:
                result.errors.append("All streams filtered out")
                return result

            # Step 3: Match streams to events
            match_result = self._match_streams(streams, group, target_date)
            result.matched_count = match_result.matched_count
            result.unmatched_count = match_result.unmatched_count
            result.cache_hits = match_result.cache_hits
            result.cache_misses = match_result.cache_misses

            # Build preview stream list
            for r in match_result.results:
                stream_id = r.stream_id if hasattr(r, "stream_id") else 0
                stream_name = r.stream_name

                preview_stream = PreviewStream(
                    stream_id=stream_id,
                    stream_name=stream_name,
                    matched=r.matched,
                    event_id=r.event.id if r.event else None,
                    event_name=r.event.name if r.event else None,
                    home_team=r.event.home_team.name if r.event else None,
                    away_team=r.event.away_team.name if r.event else None,
                    league=r.league,
                    start_time=r.event.start_time.isoformat() if r.event else None,
                    from_cache=getattr(r, "from_cache", False),
                    exclusion_reason=r.exclusion_reason,
                )
                result.streams.append(preview_stream)

            # Sort: matched first, then unmatched; within each, sort by stream name
            result.streams.sort(key=lambda s: (not s.matched, s.stream_name.lower()))

            return result

    def process_all_groups(
        self,
        target_date: date | None = None,
        run_enforcement: bool = True,
        progress_callback: Callable[[int, int, str], None] | None = None,
        generation: int | None = None,
    ) -> BatchProcessingResult:
        """Process all active event groups.

        Groups are processed in order:
        1. Parent groups (single-league, no parent_group_id) - create channels
        2. Child groups (have parent_group_id) - add streams to parent channels
        3. Multi-league groups (multiple leagues) - may consolidate with single-league

        After all groups, enforcement runs to fix any misplaced streams.

        Args:
            target_date: Target date (defaults to today)
            run_enforcement: Whether to run post-processing enforcement
            progress_callback: Optional callback(current, total, group_name)
            generation: Cache generation counter (shared across all groups)

        Returns:
            BatchProcessingResult with all group results and combined XMLTV
        """
        target_date = target_date or date.today()
        batch_result = BatchProcessingResult()
        self._generation = generation  # Store for use in _do_matching

        with self._db_factory() as conn:
            groups = get_all_groups(conn, include_disabled=False)

            # Sort groups: parents first, then children, then multi-league
            parent_groups, child_groups, multi_league_groups = self._sort_groups(groups)
            total_groups = len(parent_groups) + len(child_groups) + len(multi_league_groups)
            processed_count = 0

            # Send initial progress to avoid stall at 50%
            if progress_callback:
                if total_groups > 0:
                    progress_callback(0, total_groups, f"Found {total_groups} groups to process")
                else:
                    progress_callback(0, 1, "No event groups configured")

            processed_group_ids = []
            multi_league_ids = [g.id for g in multi_league_groups]

            # Phase 1: Process parent groups (create channels, generate EPG)
            for group in parent_groups:
                # Send "Loading..." message before expensive fetch operations
                if progress_callback:
                    leagues_count = len(group.leagues) if group.leagues else 0
                    progress_callback(
                        processed_count, total_groups,
                        f"Loading {group.name}... ({leagues_count} leagues)"
                    )

                # Create stream progress callback that reports during matching
                stream_cb = None
                if progress_callback:
                    def make_stream_cb(grp_name: str, grp_idx: int):
                        def cb(current: int, total: int, stream_name: str, matched: bool):
                            icon = "✓" if matched else "✗"
                            msg = f"{icon} {current}/{total} — {grp_name}: {stream_name}"
                            progress_callback(grp_idx, total_groups, msg)
                        return cb
                    stream_cb = make_stream_cb(group.name, processed_count + 1)

                result = self._process_group_internal(
                    conn, group, target_date, stream_progress_callback=stream_cb
                )
                batch_result.results.append(result)
                processed_group_ids.append(group.id)
                processed_count += 1
                if progress_callback:
                    # Include stream stats in progress: "Group Name (5/8 streams matched)"
                    stats = f"({result.streams_matched}/{result.streams_fetched} matched)"
                    progress_callback(processed_count, total_groups, f"{group.name} {stats}")

            # Phase 2: Process child groups (add streams to parent channels)
            for group in child_groups:
                # Send "Loading..." message before expensive fetch operations
                if progress_callback:
                    progress_callback(
                        processed_count, total_groups,
                        f"Loading {group.name}... (child group)"
                    )

                # Child groups use same stream progress pattern
                stream_cb = None
                if progress_callback:
                    def make_stream_cb(grp_name: str, grp_idx: int):
                        def cb(current: int, total: int, stream_name: str, matched: bool):
                            icon = "✓" if matched else "✗"
                            msg = f"{icon} {current}/{total} — {grp_name}: {stream_name}"
                            progress_callback(grp_idx, total_groups, msg)
                        return cb
                    stream_cb = make_stream_cb(group.name, processed_count + 1)

                result = self._process_child_group_internal(
                    conn, group, target_date, stream_progress_callback=stream_cb
                )
                batch_result.results.append(result)
                # Child groups don't generate their own XMLTV
                processed_count += 1
                if progress_callback:
                    stats = f"({result.streams_matched}/{result.streams_fetched} matched)"
                    progress_callback(processed_count, total_groups, f"{group.name} {stats}")

            # Phase 3: Process multi-league groups
            for group in multi_league_groups:
                # Send "Loading..." message before expensive fetch operations
                if progress_callback:
                    leagues_count = len(group.leagues) if group.leagues else 0
                    progress_callback(
                        processed_count, total_groups,
                        f"Loading {group.name}... ({leagues_count} leagues)"
                    )

                stream_cb = None
                if progress_callback:
                    def make_stream_cb(grp_name: str, grp_idx: int):
                        def cb(current: int, total: int, stream_name: str, matched: bool):
                            icon = "✓" if matched else "✗"
                            msg = f"{icon} {current}/{total} — {grp_name}: {stream_name}"
                            progress_callback(grp_idx, total_groups, msg)
                        return cb
                    stream_cb = make_stream_cb(group.name, processed_count + 1)

                result = self._process_group_internal(
                    conn, group, target_date, stream_progress_callback=stream_cb
                )
                batch_result.results.append(result)
                processed_group_ids.append(group.id)
                processed_count += 1
                if progress_callback:
                    stats = f"({result.streams_matched}/{result.streams_fetched} matched)"
                    progress_callback(processed_count, total_groups, f"{group.name} {stats}")

            # Phase 4: Run enforcement (keyword, cross-group, ordering, orphans)
            if run_enforcement:
                # Create lifecycle_service for orphan cleanup
                enforcement_lifecycle = None
                if self._dispatcharr_client:
                    enforcement_lifecycle = create_lifecycle_service(
                        db_factory=self._db_factory,
                        sports_service=self._service,
                        dispatcharr_client=self._dispatcharr_client,
                    )
                self._run_enforcement(
                    conn, multi_league_ids, lifecycle_service=enforcement_lifecycle
                )

            # Aggregate XMLTV from all processed groups (parents + multi-league)
            if processed_group_ids:
                xmltv_contents = get_all_group_xmltv(conn, processed_group_ids)
                if xmltv_contents:
                    batch_result.total_xmltv = merge_xmltv_content(xmltv_contents)
                    logger.info(
                        f"Aggregated XMLTV from {len(xmltv_contents)} groups, "
                        f"{len(batch_result.total_xmltv)} bytes"
                    )

        batch_result.completed_at = datetime.now()
        return batch_result

    def _sort_groups(
        self, groups: list[EventEPGGroup]
    ) -> tuple[list[EventEPGGroup], list[EventEPGGroup], list[EventEPGGroup]]:
        """Sort groups into parent, child, and multi-league categories.

        Processing order:
        1. Parent groups (single-league, no parent_group_id)
        2. Child groups (have parent_group_id)
        3. Multi-league groups (multiple leagues in leagues array)

        Args:
            groups: List of all groups

        Returns:
            Tuple of (parent_groups, child_groups, multi_league_groups)
        """
        parent_groups = []
        child_groups = []
        multi_league_groups = []

        for group in groups:
            if group.parent_group_id is not None:
                # Child group - always processed after parents
                child_groups.append(group)
            elif len(group.leagues) > 1:
                # Multi-league group - processed last
                multi_league_groups.append(group)
            else:
                # Single-league parent group - processed first
                parent_groups.append(group)

        logger.debug(
            f"Group sort: {len(parent_groups)} parents, "
            f"{len(child_groups)} children, {len(multi_league_groups)} multi-league"
        )

        return parent_groups, child_groups, multi_league_groups

    def _process_child_group_internal(
        self,
        conn: Connection,
        group: EventEPGGroup,
        target_date: date,
        stream_progress_callback: Callable | None = None,
    ) -> ProcessingResult:
        """Process a child group - adds streams to parent's channels.

        Child groups don't create their own channels or generate XMLTV.
        They match streams and add them to their parent's existing channels.

        Args:
            conn: Database connection
            group: Child group to process
            target_date: Target date
            stream_progress_callback: Optional callback(current, total, stream_name, matched)

        Returns:
            ProcessingResult with stream add details
        """
        result = ProcessingResult(group_id=group.id, group_name=group.name)

        if not group.parent_group_id:
            result.errors.append("Group is not a child group (no parent_group_id)")
            result.completed_at = datetime.now()
            return result

        # Create stats run
        stats_run = create_run(conn, run_type="event_group", group_id=group.id)

        try:
            # Step 1: Fetch M3U streams
            streams = self._fetch_streams(group)
            result.streams_fetched = len(streams)
            stats_run.streams_fetched = len(streams)

            if not streams:
                result.errors.append("No streams found for child group")
                result.completed_at = datetime.now()
                stats_run.complete(status="completed", error="No streams found")
                save_run(conn, stats_run)
                return result

            # Step 1.5: Apply stream filtering (include/exclude regex)
            streams, filter_result = self._filter_streams(streams, group)
            result.streams_after_filter = filter_result.passed_count
            result.filtered_not_event = filter_result.filtered_not_event
            result.filtered_include_regex = filter_result.filtered_include
            result.filtered_exclude_regex = filter_result.filtered_exclude

            if not streams:
                result.errors.append("All streams filtered out by regex patterns")
                result.completed_at = datetime.now()
                stats_run.complete(status="completed", error="All streams filtered")
                save_run(conn, stats_run)
                update_group_stats(
                    conn,
                    group.id,
                    stream_count=0,
                    matched_count=0,
                    filtered_include_regex=filter_result.filtered_include,
                    filtered_exclude_regex=filter_result.filtered_exclude,
                    filtered_not_event=filter_result.filtered_not_event,
                    total_stream_count=result.streams_fetched,  # V1 parity
                )
                return result

            # Step 2: Fetch events (use parent's leagues if child has none)
            leagues = group.leagues
            if not leagues:
                # Inherit from parent - need to look up parent
                from teamarr.database.groups import get_group

                parent = get_group(conn, group.parent_group_id)
                if parent:
                    leagues = parent.leagues

            events = self._fetch_events(leagues, target_date)

            if not events:
                result.errors.append(f"No events found for leagues: {leagues}")
                result.completed_at = datetime.now()
                stats_run.complete(status="completed", error="No events found")
                save_run(conn, stats_run)
                # Update stats - streams are eligible but no events to match against
                update_group_stats(
                    conn,
                    group.id,
                    stream_count=result.streams_after_filter,  # Eligible streams
                    matched_count=0,
                    filtered_include_regex=result.filtered_include_regex,
                    filtered_exclude_regex=result.filtered_exclude_regex,
                    failed_count=result.streams_after_filter,  # All unmatched due to no events
                    filtered_not_event=result.filtered_not_event,
                    total_stream_count=result.streams_fetched,
                )
                return result

            # Step 3: Match streams to events
            match_result = self._match_streams(
                streams, group, target_date,
                stream_progress_callback=stream_progress_callback,
            )
            result.streams_matched = match_result.matched_count
            result.streams_unmatched = match_result.unmatched_count
            stats_run.streams_matched = match_result.matched_count
            stats_run.streams_unmatched = match_result.unmatched_count
            stats_run.streams_cached = match_result.cache_hits

            # Save detailed match results for analysis
            self._save_match_details(
                conn=conn,
                run_id=stats_run.id,
                group_id=group.id,
                group_name=group.name,
                streams=streams,
                match_result=match_result,
            )

            # Step 4: Add matched streams to parent's channels
            matched_streams = self._build_matched_stream_list(streams, match_result)
            if matched_streams:
                child_processor = self._get_child_processor()
                child_result = child_processor.process_child_streams(
                    child_group={"id": group.id, "name": group.name},
                    parent_group_id=group.parent_group_id,
                    matched_streams=matched_streams,
                )

                # Map child result to processing result
                result.channels_created = 0  # Child groups don't create channels
                result.channels_existing = child_result.added_count
                result.channels_skipped = child_result.skipped_count
                result.channel_errors = child_result.error_count

                stats_run.channels_created = 0
                stats_run.channels_updated = child_result.added_count
                stats_run.channels_skipped = child_result.skipped_count
                stats_run.channels_errors = child_result.error_count

                for error in child_result.errors:
                    result.errors.append(f"Child stream error: {error}")

            # No XMLTV generation for child groups
            result.programmes_generated = 0

            stats_run.complete(status="completed")

            # Update group's processing stats
            update_group_stats(
                conn,
                group.id,
                stream_count=result.streams_after_filter,
                matched_count=result.streams_matched,
                filtered_include_regex=result.filtered_include_regex,
                filtered_exclude_regex=result.filtered_exclude_regex,
                failed_count=result.streams_unmatched,
                filtered_not_event=result.filtered_not_event,
                streams_excluded=result.streams_excluded,
                total_stream_count=result.streams_fetched,  # V1 parity
                excluded_event_final=result.excluded_event_final,
                excluded_event_past=result.excluded_event_past,
                excluded_before_window=result.excluded_before_window,
                excluded_league_not_included=result.excluded_league_not_included,
            )

        except Exception as e:
            logger.exception(f"Error processing child group {group.name}")
            result.errors.append(str(e))
            stats_run.complete(status="failed", error=str(e))

        save_run(conn, stats_run)
        result.completed_at = datetime.now()
        return result

    def _get_child_processor(self) -> ChildStreamProcessor:
        """Get or create ChildStreamProcessor instance."""
        channel_manager = self._dispatcharr_client.channels if self._dispatcharr_client else None

        return ChildStreamProcessor(
            db_factory=self._db_factory,
            channel_manager=channel_manager,
        )

    def _run_enforcement(
        self,
        conn: Connection,
        multi_league_ids: list[int],
        lifecycle_service=None,
    ) -> None:
        """Run post-processing enforcement.

        V1 Parity: Runs every EPG generation:
        1. Keyword enforcement: ensure streams are on correct keyword channels
        2. Cross-group consolidation: merge multi-league into single-league
        3. Keyword ordering: ensure main channel < keyword channels in numbering
        4. Orphan cleanup: delete Dispatcharr channels not tracked in DB
        5. Disabled group cleanup: delete channels from disabled groups

        Args:
            conn: Database connection
            multi_league_ids: IDs of multi-league groups for cross-group check
            lifecycle_service: Optional lifecycle service for orphan/disabled cleanup
        """
        channel_manager = self._dispatcharr_client.channels if self._dispatcharr_client else None

        # 1. Keyword enforcement: move streams to correct keyword channels
        try:
            keyword_enforcer = KeywordEnforcer(self._db_factory, channel_manager)
            keyword_result = keyword_enforcer.enforce()
            if keyword_result.moved_count > 0:
                logger.info(f"Keyword enforcement moved {keyword_result.moved_count} streams")
        except Exception as e:
            logger.warning(f"Keyword enforcement failed: {e}")

        # 2. Cross-group consolidation (only if multi-league groups exist)
        if multi_league_ids:
            try:
                cross_group_enforcer = CrossGroupEnforcer(self._db_factory, channel_manager)
                cross_result = cross_group_enforcer.enforce(multi_league_ids)
                if cross_result.deleted_count > 0:
                    logger.info(
                        f"Cross-group consolidation: {cross_result.deleted_count} channels merged"
                    )
            except Exception as e:
                logger.warning(f"Cross-group consolidation failed: {e}")

        # 3. Keyword ordering: ensure main channel has lower number than keyword channels
        try:
            ordering_enforcer = KeywordOrderingEnforcer(self._db_factory, channel_manager)
            ordering_result = ordering_enforcer.enforce()
            if ordering_result.reordered_count > 0:
                logger.info(
                    f"Keyword ordering: reordered {ordering_result.reordered_count} channel pair(s)"
                )
        except Exception as e:
            logger.warning(f"Keyword ordering failed: {e}")

        # 4. Orphan cleanup: delete Dispatcharr channels not tracked in DB
        if lifecycle_service:
            try:
                orphan_result = lifecycle_service.cleanup_orphan_dispatcharr_channels()
                if orphan_result.get("deleted", 0) > 0:
                    logger.info(
                        f"Orphan cleanup: deleted {orphan_result['deleted']} Dispatcharr channels"
                    )
            except Exception as e:
                logger.warning(f"Orphan cleanup failed: {e}")

        # 5. Disabled group cleanup: delete channels from disabled groups
        if lifecycle_service:
            try:
                disabled_result = lifecycle_service.cleanup_disabled_groups()
                if disabled_result.get("deleted"):
                    logger.info(
                        f"Disabled group cleanup: deleted "
                        f"{len(disabled_result['deleted'])} channels"
                    )
            except Exception as e:
                logger.warning(f"Disabled group cleanup failed: {e}")

    def _process_group_internal(
        self,
        conn: Connection,
        group: EventEPGGroup,
        target_date: date,
        stream_progress_callback: Callable | None = None,
    ) -> ProcessingResult:
        """Internal processing for a single group.

        Args:
            conn: Database connection
            group: Event group to process
            target_date: Target date for matching
            stream_progress_callback: Optional callback(current, total, stream_name, matched)
        """
        result = ProcessingResult(group_id=group.id, group_name=group.name)

        # Create stats run for tracking
        stats_run = create_run(conn, run_type="event_group", group_id=group.id)

        try:
            # Step 1: Fetch M3U streams from Dispatcharr
            streams = self._fetch_streams(group)
            result.streams_fetched = len(streams)
            stats_run.streams_fetched = len(streams)

            if not streams:
                result.errors.append("No streams found for group")
                result.completed_at = datetime.now()
                stats_run.complete(status="completed", error="No streams found")
                save_run(conn, stats_run)
                return result

            # Step 1.5: Apply stream filtering (include/exclude regex)
            streams, filter_result = self._filter_streams(streams, group)
            result.streams_after_filter = filter_result.passed_count
            result.filtered_not_event = filter_result.filtered_not_event
            result.filtered_include_regex = filter_result.filtered_include
            result.filtered_exclude_regex = filter_result.filtered_exclude

            if not streams:
                result.errors.append("All streams filtered out by regex patterns")
                result.completed_at = datetime.now()
                stats_run.complete(status="completed", error="All streams filtered")
                save_run(conn, stats_run)
                # Still update stats even if all filtered
                update_group_stats(
                    conn,
                    group.id,
                    stream_count=0,
                    matched_count=0,
                    filtered_include_regex=filter_result.filtered_include,
                    filtered_exclude_regex=filter_result.filtered_exclude,
                    filtered_not_event=filter_result.filtered_not_event,
                    total_stream_count=result.streams_fetched,  # V1 parity
                )
                return result

            # Step 2: Fetch events from data providers
            events = self._fetch_events(group.leagues, target_date)
            logger.info(
                f"Fetched {len(events)} events for group '{group.name}' leagues={group.leagues}"
            )

            if not events:
                result.errors.append(f"No events found for leagues: {group.leagues}")
                result.completed_at = datetime.now()
                stats_run.complete(status="completed", error="No events found")
                save_run(conn, stats_run)
                # Update stats - streams are eligible but no events to match against
                update_group_stats(
                    conn,
                    group.id,
                    stream_count=result.streams_after_filter,  # Eligible streams
                    matched_count=0,
                    filtered_include_regex=filter_result.filtered_include,
                    filtered_exclude_regex=filter_result.filtered_exclude,
                    failed_count=result.streams_after_filter,  # All unmatched due to no events
                    filtered_not_event=filter_result.filtered_not_event,
                    total_stream_count=result.streams_fetched,
                )
                return result

            # Step 3: Match streams to events (uses fingerprint cache)
            match_result = self._match_streams(
                streams, group, target_date,
                stream_progress_callback=stream_progress_callback,
            )
            result.streams_matched = match_result.matched_count
            result.streams_unmatched = match_result.unmatched_count
            stats_run.streams_matched = match_result.matched_count
            stats_run.streams_unmatched = match_result.unmatched_count
            stats_run.streams_cached = match_result.cache_hits

            # Count matcher-level exclusions (matched but excluded by league/event_final)
            for r in match_result.results:
                if r.matched and not r.included and r.exclusion_reason:
                    result.streams_excluded += 1
                    if r.exclusion_reason == "event_final":
                        result.excluded_event_final += 1
                    elif r.exclusion_reason.startswith("league_not_included"):
                        result.excluded_league_not_included += 1

            # Save detailed match results for analysis
            self._save_match_details(
                conn=conn,
                run_id=stats_run.id,
                group_id=group.id,
                group_name=group.name,
                streams=streams,
                match_result=match_result,
            )

            # Step 4: Create/update channels
            matched_streams = self._build_matched_stream_list(streams, match_result)

            # Sort channels based on group's channel_sort_order setting
            matched_streams = self._sort_matched_streams(matched_streams, group.channel_sort_order)

            # Extract all stream IDs for cleanup (V1 parity: cleanup missing streams)
            all_stream_ids = [s.get("id") for s in streams if s.get("id")]

            if matched_streams:
                lifecycle_result = self._process_channels(
                    matched_streams, group, conn, all_stream_ids=all_stream_ids
                )
                result.channels_created = len(lifecycle_result.created)
                result.channels_existing = len(lifecycle_result.existing)
                result.channels_skipped = len(lifecycle_result.skipped)
                result.channels_deleted = len(lifecycle_result.deleted)
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

                stats_run.channels_created = len(lifecycle_result.created)
                stats_run.channels_updated = len(lifecycle_result.existing)
                stats_run.channels_skipped = len(lifecycle_result.skipped)
                stats_run.channels_deleted = len(lifecycle_result.deleted)
                stats_run.channels_errors = len(lifecycle_result.errors)

                for error in lifecycle_result.errors:
                    result.errors.append(f"Channel error: {error}")

                # Step 5: Generate XMLTV from matched streams
                # Filter out streams excluded by lifecycle (event_final, event_past, etc.)
                excluded_event_ids = {
                    excl.get("event_id") for excl in lifecycle_result.excluded
                    if excl.get("event_id")
                }
                xmltv_streams = [
                    ms for ms in matched_streams
                    if ms.get("event") and ms["event"].id not in excluded_event_ids
                ]

                xmltv_content, programmes_total, event_programmes, pregame, postgame = (
                    self._generate_xmltv(xmltv_streams, group, conn)
                )
                result.programmes_generated = programmes_total
                result.events_count = event_programmes
                result.pregame_count = pregame
                result.postgame_count = postgame
                result.xmltv_size = len(xmltv_content.encode("utf-8")) if xmltv_content else 0

                stats_run.programmes_total = programmes_total
                stats_run.programmes_events = event_programmes
                stats_run.programmes_pregame = pregame
                stats_run.programmes_postgame = postgame
                stats_run.xmltv_size_bytes = result.xmltv_size

                # Step 6: Store XMLTV for this group (in database)
                if xmltv_content:
                    self._store_group_xmltv(conn, group.id, xmltv_content)

                # Step 7: Trigger Dispatcharr refresh if configured
                if xmltv_content and self._dispatcharr_client:
                    self._trigger_epg_refresh(group)

            # Mark run as completed successfully
            stats_run.complete(status="completed")

            # Update group's processing stats
            update_group_stats(
                conn,
                group.id,
                stream_count=result.streams_after_filter,
                matched_count=result.streams_matched,
                filtered_include_regex=result.filtered_include_regex,
                filtered_exclude_regex=result.filtered_exclude_regex,
                failed_count=result.streams_unmatched,
                filtered_not_event=result.filtered_not_event,
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
            stats_run.complete(status="failed", error=str(e))

        # Save stats run
        save_run(conn, stats_run)

        result.completed_at = datetime.now()
        return result

    def _fetch_streams(self, group: EventEPGGroup) -> list[dict]:
        """Fetch M3U streams from Dispatcharr for the group.

        Uses group's m3u_group_id to filter streams.
        """
        if not self._dispatcharr_client:
            logger.warning("Dispatcharr not configured - cannot fetch streams")
            return []

        try:
            m3u_manager = self._dispatcharr_client.m3u

            # Fetch streams filtered by M3U group if configured
            if group.m3u_group_id:
                streams = m3u_manager.list_streams(group_id=group.m3u_group_id)
            else:
                # Fetch all streams if no group filter
                streams = m3u_manager.list_streams()

            # Convert to dicts for matcher (sorted by name for consistent order)
            stream_dicts = [
                {
                    "id": s.id,
                    "name": s.name,
                    "tvg_id": s.tvg_id,
                    "tvg_name": s.tvg_name,
                    "channel_group": s.channel_group,
                    "channel_group_id": s.channel_group_id,
                    "m3u_account_id": s.m3u_account_id,
                }
                for s in streams
            ]
            # Sort by stream ID ascending for consistent processing order
            stream_dicts.sort(key=lambda s: s["id"])
            return stream_dicts

        except Exception as e:
            logger.error(f"Failed to fetch streams: {e}")
            return []

    def _filter_streams(
        self,
        streams: list[dict],
        group: EventEPGGroup,
    ) -> tuple[list[dict], FilterResult]:
        """Filter streams using global settings and group's regex configuration.

        Global settings apply first (event pattern filter), then group-specific.

        Args:
            streams: List of stream dicts from Dispatcharr
            group: Event group with filter configuration

        Returns:
            Tuple of (filtered_streams, filter_result)
        """
        from teamarr.database.settings import get_stream_filter_settings
        from teamarr.services.stream_filter import StreamFilter, StreamFilterConfig

        # Load global stream filter settings
        with self._db_factory() as conn:
            global_settings = get_stream_filter_settings(conn)

        # Build config combining global and group settings
        config = StreamFilterConfig(
            # Global event pattern filter (enabled by default)
            require_event_pattern=global_settings.require_event_pattern,
            # Group-specific include regex (if enabled)
            include_regex=group.stream_include_regex,
            include_enabled=group.stream_include_regex_enabled,
            # Group-specific exclude regex (if enabled)
            exclude_regex=group.stream_exclude_regex,
            exclude_enabled=group.stream_exclude_regex_enabled,
            # Group-specific team extraction
            custom_teams_regex=group.custom_regex_teams,
            custom_teams_enabled=group.custom_regex_teams_enabled,
            skip_builtin=group.skip_builtin_filter,
        )

        stream_filter = StreamFilter(config)
        result = stream_filter.filter(streams)

        # Log filtering results
        filtered_total = (
            result.filtered_include + result.filtered_exclude + result.filtered_not_event
        )
        if filtered_total > 0:
            logger.info(
                f"Filtered streams for group '{group.name}': "
                f"{result.total_input} input → {result.passed_count} passed "
                f"(not_event: -{result.filtered_not_event}, "
                f"include: -{result.filtered_include}, exclude: -{result.filtered_exclude})"
            )

        return result.passed, result

    def _get_all_enabled_leagues(self) -> list[str]:
        """Get all enabled leagues from the database.

        Used to search all possible leagues when matching streams,
        rather than just the group's configured leagues.
        """
        with self._db_factory() as conn:
            cursor = conn.execute("SELECT league_code FROM leagues WHERE enabled = 1")
            return [row[0] for row in cursor.fetchall()]

    def _fetch_events(self, leagues: list[str], target_date: date) -> list[Event]:
        """Fetch events from data providers for leagues in parallel.

        Uses a fixed 7-day lookback (for weekly sports like NFL) and
        event_match_days_ahead setting for future events.
        """
        if not leagues:
            return []

        all_events: list[Event] = []
        num_workers = min(MAX_WORKERS, len(leagues))

        # Load date range settings
        # Note: days_back is hardcoded to 7 for weekly sports like NFL
        with self._db_factory() as conn:
            row = conn.execute(
                "SELECT event_match_days_ahead FROM settings WHERE id = 1"
            ).fetchone()
            days_back = 7  # Hardcoded for weekly sports
            days_ahead = row["event_match_days_ahead"] if row and row["event_match_days_ahead"] else 3

        # Build date range: [target - days_back, target + days_ahead]
        dates_to_fetch = [
            target_date + timedelta(days=offset)
            for offset in range(-days_back, days_ahead + 1)
        ]
        logger.debug(f"Fetching events from {dates_to_fetch[0]} to {dates_to_fetch[-1]} ({len(dates_to_fetch)} days)")

        def fetch_league_events(league: str, fetch_date: date) -> tuple[str, date, list[Event]]:
            """Fetch events for a single league/date (for parallel execution)."""
            try:
                # TSDB leagues: cache-only (don't hit API during EPG generation)
                # TSDB cache builds organically from startup/scheduled refresh
                is_tsdb = self._service.get_provider_name(league) == "tsdb"
                events = self._service.get_events(league, fetch_date, cache_only=is_tsdb)
                return (league, fetch_date, events)
            except Exception as e:
                logger.warning(f"Failed to fetch events for {league} on {fetch_date}: {e}")
                return (league, fetch_date, [])

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            # Create tasks for all league/date combinations
            futures = {}
            for league in leagues:
                for fetch_date in dates_to_fetch:
                    future = executor.submit(fetch_league_events, league, fetch_date)
                    futures[future] = (league, fetch_date)

            for future in as_completed(futures):
                try:
                    league, fetch_date, events = future.result()
                    all_events.extend(events)
                except Exception as e:
                    league, fetch_date = futures[future]
                    logger.warning(f"Failed to fetch events for {league} on {fetch_date}: {e}")

        return all_events

    def _match_streams(
        self,
        streams: list[dict],
        group: EventEPGGroup,
        target_date: date,
        stream_progress_callback: Callable | None = None,
    ) -> BatchMatchResult:
        """Match streams to events using StreamMatcher.

        Uses fingerprint cache - streams only need to be matched once
        unless stream name changes.

        Important: We search ALL enabled leagues to find matches, but only
        include events from the group's configured leagues. This allows
        multi-sport groups to match any event while filtering output.

        Args:
            streams: List of stream dicts
            group: Event EPG group (contains leagues, custom regex, etc.)
            target_date: Date to match events for
            stream_progress_callback: Optional callback(current, total, stream_name, matched)
        """
        # Get all enabled leagues to search (not just the group's configured leagues)
        all_leagues = self._get_all_enabled_leagues()

        # Load settings for event filtering
        with self._db_factory() as conn:
            row = conn.execute(
                "SELECT include_final_events FROM settings WHERE id = 1"
            ).fetchone()
            include_final_events = bool(row["include_final_events"]) if row else False

        sport_durations = self._load_sport_durations_cached()

        matcher = StreamMatcher(
            service=self._service,
            db_factory=self._db_factory,
            group_id=group.id,
            search_leagues=all_leagues,  # Search ALL leagues
            include_leagues=group.leagues,  # Filter to group's configured leagues
            include_final_events=include_final_events,
            sport_durations=sport_durations,
            generation=getattr(self, "_generation", None),  # Use shared generation if set
            custom_regex_teams=group.custom_regex_teams,
            custom_regex_teams_enabled=group.custom_regex_teams_enabled,
        )

        result = matcher.match_all(streams, target_date, progress_callback=stream_progress_callback)

        # Purge stale cache entries at end of match
        matcher.purge_stale()

        return result

    def _load_sport_durations_cached(self) -> dict[str, float]:
        """Load sport durations (cached for reuse within a run)."""
        if not hasattr(self, "_sport_durations_cache"):
            with self._db_factory() as conn:
                self._sport_durations_cache = self._load_sport_durations(conn)
        return self._sport_durations_cache

    def _build_matched_stream_list(
        self,
        streams: list[dict],
        match_result: BatchMatchResult,
    ) -> list[dict]:
        """Build list of matched streams with their events.

        Returns list of dicts with 'stream' and 'event' keys.
        """
        # Build name -> stream lookup
        stream_lookup = {s["name"]: s for s in streams}

        matched = []
        for result in match_result.results:
            if result.matched and result.included and result.event:
                stream = stream_lookup.get(result.stream_name)
                if stream:
                    matched.append(
                        {
                            "stream": stream,
                            "event": result.event,
                        }
                    )

        return matched

    def _sort_matched_streams(
        self,
        matched_streams: list[dict],
        sort_order: str,
    ) -> list[dict]:
        """Sort matched streams based on channel_sort_order setting.

        Sort orders:
        - 'time': Sort by event start time (default)
        - 'sport_time': Sort by sport first, then start time
        - 'league_time': Sort by league first, then start time

        Args:
            matched_streams: List of {'stream': ..., 'event': ...} dicts
            sort_order: One of 'time', 'sport_time', 'league_time'

        Returns:
            Sorted list of matched streams
        """
        if not matched_streams:
            return matched_streams

        # Default fallback values for missing data
        max_time = datetime.max.replace(tzinfo=None)

        def get_start_time(m: dict) -> datetime:
            """Get event start time, handling timezone-aware datetimes."""
            event = m.get("event")
            if not event:
                return max_time
            start = event.start_time
            # Make timezone-naive for comparison
            if start and start.tzinfo:
                return start.replace(tzinfo=None)
            return start or max_time

        if sort_order == "sport_time":
            # Sort by sport (alphabetically), then by start time
            def sort_key(m: dict):
                event = m.get("event")
                sport = event.sport.lower() if event and event.sport else "zzz"
                return (sport, get_start_time(m))

            return sorted(matched_streams, key=sort_key)

        elif sort_order == "league_time":
            # Sort by league (alphabetically), then by start time
            def sort_key(m: dict):
                event = m.get("event")
                league = event.league.lower() if event and event.league else "zzz"
                return (league, get_start_time(m))

            return sorted(matched_streams, key=sort_key)

        else:
            # Default: sort by time only
            return sorted(matched_streams, key=get_start_time)

    def _save_match_details(
        self,
        conn: Connection,
        run_id: int,
        group_id: int,
        group_name: str,
        streams: list[dict],
        match_result: BatchMatchResult,
        filter_result: FilterResult | None = None,
    ) -> None:
        """Save detailed match results to database.

        Stores both matched streams and failed/unmatched streams for analysis.
        """
        # Build name -> stream lookup for stream IDs
        stream_lookup = {s["name"]: s for s in streams}

        matched_list: list[MatchedStream] = []
        failed_list: list[FailedMatch] = []

        for result in match_result.results:
            stream = stream_lookup.get(result.stream_name, {})
            stream_id = stream.get("id")

            if result.matched and result.included and result.event:
                # Successfully matched and included
                event_date = (
                    result.event.start_time.isoformat() if result.event.start_time else None
                )
                # Extract match method and confidence if available (Phase 7 enhancement)
                match_method = getattr(result, "match_method", None)
                if match_method and hasattr(match_method, "value"):
                    match_method = match_method.value  # Convert enum to string
                confidence = getattr(result, "confidence", None)

                matched_list.append(
                    MatchedStream(
                        run_id=run_id,
                        group_id=group_id,
                        group_name=group_name,
                        stream_id=stream_id,
                        stream_name=result.stream_name,
                        event_id=result.event.id,
                        event_name=result.event.name,
                        event_date=event_date,
                        detected_league=result.league,
                        home_team=result.event.home_team.name if result.event.home_team else None,
                        away_team=result.event.away_team.name if result.event.away_team else None,
                        from_cache=getattr(result, "from_cache", False),
                        match_method=match_method,
                        confidence=confidence,
                        origin_match_method=getattr(result, "origin_match_method", None),
                    )
                )
            elif result.matched and not result.included:
                # Matched but excluded (wrong league)
                failed_list.append(
                    FailedMatch(
                        run_id=run_id,
                        group_id=group_id,
                        group_name=group_name,
                        stream_id=stream_id,
                        stream_name=result.stream_name,
                        reason="excluded_league",
                        exclusion_reason=result.exclusion_reason,
                        detail=f"League: {result.league}",
                        detected_league=result.league,
                    )
                )
            elif result.is_exception:
                # Exception keyword stream
                failed_list.append(
                    FailedMatch(
                        run_id=run_id,
                        group_id=group_id,
                        group_name=group_name,
                        stream_id=stream_id,
                        stream_name=result.stream_name,
                        reason="exception",
                        detail=f"Keyword: {result.exception_keyword}",
                    )
                )
            else:
                # Unmatched - extract parsed teams if available (Phase 7 enhancement)
                parsed_team1 = getattr(result, "parsed_team1", None)
                parsed_team2 = getattr(result, "parsed_team2", None)
                detected_league = getattr(result, "detected_league", None)

                failed_list.append(
                    FailedMatch(
                        run_id=run_id,
                        group_id=group_id,
                        group_name=group_name,
                        stream_id=stream_id,
                        stream_name=result.stream_name,
                        reason="unmatched",
                        parsed_team1=parsed_team1,
                        parsed_team2=parsed_team2,
                        detected_league=detected_league,
                    )
                )

        # Save to database
        if matched_list:
            save_matched_streams(conn, matched_list)
            logger.debug(f"Saved {len(matched_list)} matched streams for group {group_name}")

        if failed_list:
            save_failed_matches(conn, failed_list)
            logger.debug(f"Saved {len(failed_list)} failed matches for group {group_name}")

    def _process_channels(
        self,
        matched_streams: list[dict],
        group: EventEPGGroup,
        conn: Connection,
        all_stream_ids: list[int] | None = None,
    ) -> StreamProcessResult:
        """Create/update channels via ChannelLifecycleService.

        V1 Parity: Full lifecycle management with every generation:
        1. Process scheduled deletions (expired channels)
        2. Cleanup deleted streams (missing from M3U)
        3. Create/update channels
        4. Sync existing channel settings
        5. Reassign channel numbers if needed
        """
        from teamarr.consumers.lifecycle import StreamProcessResult

        lifecycle_service = create_lifecycle_service(
            self._db_factory,
            self._service,  # Required for template resolution
            self._dispatcharr_client,
        )

        # Build group config dict
        group_config = {
            "id": group.id,
            "duplicate_event_handling": group.duplicate_event_handling,
            "channel_group_id": group.channel_group_id,
            "stream_profile_id": group.stream_profile_id,
            "channel_profile_ids": group.channel_profile_ids,
            "channel_start_number": group.channel_start_number,
            # For cross-group consolidation
            "overlap_handling": group.overlap_handling,
            "leagues": group.leagues,  # len > 1 means multi-league
            "m3u_account_id": group.m3u_account_id,
            "m3u_account_name": group.m3u_account_name,
        }

        # Load template from database if configured
        template_config = None
        if group.template_id:
            template_config = self._load_event_template(conn, group.template_id)

        combined_result = StreamProcessResult()

        # V1 Parity Step 0: Global AUTO range reassignment
        # This ensures all AUTO groups have correct non-overlapping ranges
        # Must happen BEFORE creating new channels to avoid range conflicts
        try:
            reassign_result = lifecycle_service.reassign_all_auto_groups()
            if reassign_result.get("channels_reassigned"):
                logger.info(
                    f"Global reassignment: moved {reassign_result['channels_reassigned']} "
                    f"channels across {reassign_result['groups_processed']} groups"
                )
        except Exception as e:
            logger.debug(f"Error in global AUTO reassignment: {e}")

        # V1 Parity Step 1: Process scheduled deletions first
        try:
            deletion_result = lifecycle_service.process_scheduled_deletions()
            combined_result.merge(deletion_result)
            if deletion_result.deleted:
                logger.info(f"Deleted {len(deletion_result.deleted)} expired channels")
        except Exception as e:
            logger.debug(f"Error processing scheduled deletions: {e}")

        # V1 Parity Step 2: Cleanup deleted/missing streams
        if all_stream_ids is not None:
            try:
                cleanup_result = lifecycle_service.cleanup_deleted_streams(group.id, all_stream_ids)
                combined_result.merge(cleanup_result)
                if cleanup_result.deleted:
                    logger.info(
                        f"Deleted {len(cleanup_result.deleted)} channels with missing streams"
                    )
            except Exception as e:
                logger.debug(f"Error cleaning up deleted streams: {e}")

        # V1 Parity Step 3-4: Create new channels and sync existing settings
        process_result = lifecycle_service.process_matched_streams(
            matched_streams, group_config, template_config
        )
        combined_result.merge(process_result)

        # V1 Parity Step 5: Reassign channel numbers to compact range
        try:
            reassign_result = lifecycle_service.reassign_group_channels(group.id)
            if reassign_result.get("reassigned"):
                logger.info(
                    f"Reassigned {len(reassign_result['reassigned'])} channels in group {group.id}"
                )
        except Exception as e:
            logger.debug(f"Error reassigning channel numbers: {e}")

        return combined_result

    def _load_event_template(self, conn: Connection, template_id: int):
        """Load and convert template for event-based EPG.

        Args:
            conn: Database connection
            template_id: Template ID to load

        Returns:
            EventTemplateConfig or None if template not found
        """
        from teamarr.database.templates import get_template, template_to_event_config

        template = get_template(conn, template_id)
        if not template:
            logger.warning(f"Template {template_id} not found")
            return None

        return template_to_event_config(template)

    def _generate_xmltv(
        self,
        matched_streams: list[dict],
        group: EventEPGGroup,
        conn: Connection,
    ) -> tuple[str, int, int, int, int]:
        """Generate XMLTV content from matched streams.

        Args:
            matched_streams: List of matched stream/event dicts
            group: Event group config
            conn: Database connection

        Returns:
            Tuple of (xmltv_content, total_programmes, event_programmes, pregame, postgame)
        """
        if not matched_streams:
            return "", 0, 0, 0, 0

        # Load template options if configured
        options = EventEPGOptions()
        filler_config: EventFillerConfig | None = None
        template_db = None

        if group.template_id:
            template_config = self._load_event_template(conn, group.template_id)
            if template_config:
                options.template = template_config

            # Load raw template for filler config
            from teamarr.database.templates import get_template

            template_db = get_template(conn, group.template_id)
            if template_db and (template_db.pregame_enabled or template_db.postgame_enabled):
                filler_config = template_to_event_filler_config(template_db)

        # Load sport durations from settings
        options.sport_durations = self._load_sport_durations(conn)

        # Generate programmes and channels from matched streams
        programmes, channels = self._epg_generator.generate_for_matched_streams(
            matched_streams, options
        )

        if not programmes:
            return "", 0, 0, 0, 0

        # Track event programmes separately
        event_programmes_count = len(programmes)
        pregame_count = 0
        postgame_count = 0

        # Generate filler if enabled in template
        if filler_config:
            filler_result = self._generate_filler_for_streams(
                matched_streams, filler_config, options.sport_durations
            )
            if filler_result.programmes:
                pregame_count = filler_result.pregame_count
                postgame_count = filler_result.postgame_count
                programmes.extend(filler_result.programmes)
                # Sort all programmes by channel_id then start time
                programmes.sort(key=lambda p: (p.channel_id, p.start))
                logger.debug(
                    f"Added {len(filler_result.programmes)} filler programmes "
                    f"({pregame_count} pregame, {postgame_count} postgame) "
                    f"for group '{group.name}'"
                )

        # Convert to XMLTV
        channel_dicts = [{"id": ch.channel_id, "name": ch.name, "icon": ch.icon} for ch in channels]
        xmltv_content = programmes_to_xmltv(programmes, channel_dicts)

        filler_total = pregame_count + postgame_count
        logger.info(
            f"Generated XMLTV for group '{group.name}': "
            f"{event_programmes_count} events + {filler_total} filler = "
            f"{len(programmes)} programmes, {len(xmltv_content)} bytes"
        )

        return xmltv_content, len(programmes), event_programmes_count, pregame_count, postgame_count

    def _load_sport_durations(self, conn: Connection) -> dict[str, float]:
        """Load sport duration settings from database."""
        row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        if not row:
            return {}

        settings = dict(row)
        return {
            "basketball": settings.get("duration_basketball", 3.0),
            "football": settings.get("duration_football", 3.5),
            "hockey": settings.get("duration_hockey", 3.0),
            "baseball": settings.get("duration_baseball", 3.5),
            "soccer": settings.get("duration_soccer", 2.5),
            "mma": settings.get("duration_mma", 4.0),
            "boxing": settings.get("duration_boxing", 4.0),
        }

    def _generate_filler_for_streams(
        self,
        matched_streams: list[dict],
        filler_config: EventFillerConfig,
        sport_durations: dict[str, float],
    ) -> EventFillerResult:
        """Generate filler programmes for matched event streams.

        Args:
            matched_streams: List of matched stream/event dicts
            filler_config: Filler configuration from template
            sport_durations: Sport duration settings

        Returns:
            EventFillerResult with programmes and pregame/postgame counts
        """
        from teamarr.config import get_user_timezone

        filler_generator = EventFillerGenerator(self._service)
        result = EventFillerResult()

        # Get configured timezone
        tz = get_user_timezone()

        # Build filler options
        now = datetime.now(tz)
        options = EventFillerOptions(
            epg_start=now,
            epg_end=now + timedelta(days=1),  # 24 hour window
            epg_timezone=str(tz),
            sport_durations=sport_durations,
            default_duration=3.0,
            postgame_buffer_hours=24.0,
        )

        for stream_match in matched_streams:
            event = stream_match.get("event")

            if not event:
                continue

            # Use consistent tvg_id matching EventEPGGenerator and ChannelLifecycleService
            from teamarr.consumers.lifecycle import generate_event_tvg_id

            channel_id = generate_event_tvg_id(event.id, event.provider)

            try:
                filler_result = filler_generator.generate_with_counts(
                    event=event,
                    channel_id=channel_id,
                    config=filler_config,
                    options=options,
                )
                result.programmes.extend(filler_result.programmes)
                result.pregame_count += filler_result.pregame_count
                result.postgame_count += filler_result.postgame_count
            except Exception as e:
                logger.warning(f"Failed to generate filler for event {event.id}: {e}")

        return result

    def _store_group_xmltv(
        self,
        conn: Connection,
        group_id: int,
        xmltv_content: str,
    ) -> None:
        """Store XMLTV content for a group in the database.

        This allows the XMLTV to be served at a predictable URL
        that Dispatcharr can fetch.
        """
        # Upsert into event_epg_xmltv table
        conn.execute(
            """
            INSERT INTO event_epg_xmltv (group_id, xmltv_content, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(group_id) DO UPDATE SET
                xmltv_content = excluded.xmltv_content,
                updated_at = datetime('now')
            """,
            (group_id, xmltv_content),
        )
        conn.commit()
        logger.debug(f"Stored XMLTV for group {group_id}")

    def _trigger_epg_refresh(self, group: EventEPGGroup) -> None:
        """Trigger Dispatcharr EPG refresh and associate EPG with channels.

        Dispatcharr needs to re-fetch the XMLTV from Teamarr's endpoint
        and import it into its EPG data store. After refresh completes,
        we associate the EPG data with managed channels by tvg_id.
        """
        if not self._dispatcharr_client:
            return

        try:
            from teamarr.database import get_db

            # Get EPG source ID from settings
            with get_db() as conn:
                row = conn.execute(
                    "SELECT dispatcharr_epg_id FROM settings WHERE id = 1"
                ).fetchone()

            epg_source_id = row["dispatcharr_epg_id"] if row else None

            if not epg_source_id:
                logger.debug("No Dispatcharr EPG source configured - skipping refresh")
                return

            epg_manager = self._dispatcharr_client.epg

            # Wait for refresh to complete (polls until done or timeout)
            result = epg_manager.wait_for_refresh(epg_source_id, timeout=120)

            if result.success:
                logger.info(
                    f"Dispatcharr EPG refresh completed for source {epg_source_id} "
                    f"in {result.duration:.1f}s"
                )

                # Now associate EPG data with managed channels
                self._associate_epg_with_channels(epg_source_id)
            else:
                logger.warning(f"EPG refresh failed: {result.message}")

        except Exception as e:
            logger.warning(f"Error during EPG refresh: {e}")

    def _associate_epg_with_channels(self, epg_source_id: int) -> None:
        """Associate EPG data with managed channels after EPG refresh.

        Looks up EPG data by tvg_id and links them to channels in Dispatcharr.
        """
        from teamarr.database.channels import get_all_managed_channels

        try:
            channel_manager = self._dispatcharr_client.channels

            with self._db_factory() as conn:
                # Get all active managed channels
                channels = get_all_managed_channels(conn, include_deleted=False)

                if not channels:
                    logger.debug("No managed channels to associate EPG with")
                    return

                # Build EPG data lookup from Dispatcharr
                epg_lookup = channel_manager.build_epg_lookup(epg_source_id)

                if not epg_lookup:
                    logger.debug("No EPG data found in Dispatcharr to associate")
                    return

                associated = 0
                not_found = 0

                for channel in channels:
                    if not channel.dispatcharr_channel_id or not channel.tvg_id:
                        continue

                    # Look up EPG data by tvg_id
                    epg_data = epg_lookup.get(channel.tvg_id)

                    if not epg_data:
                        not_found += 1
                        continue

                    # Associate EPG with channel
                    epg_data_id = epg_data.get("id")
                    if not epg_data_id:
                        not_found += 1
                        continue

                    try:
                        result = channel_manager.set_channel_epg(
                            channel.dispatcharr_channel_id,
                            epg_data_id,
                        )
                        if result.success:
                            associated += 1
                        else:
                            logger.debug(
                                f"Failed to set EPG for channel "
                                f"{channel.channel_name}: {result.error}"
                            )
                    except Exception as e:
                        logger.debug(
                            f"Failed to associate EPG for channel {channel.channel_name}: {e}"
                        )

                if associated:
                    logger.info(f"Associated EPG data with {associated} channels")
                if not_found:
                    logger.debug(f"EPG data not found for {not_found} channels (pending refresh)")

        except Exception as e:
            logger.warning(f"Error associating EPG with channels: {e}")


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


def process_event_group(
    db_factory: Any,
    group_id: int,
    dispatcharr_client: Any = None,
    target_date: date | None = None,
) -> ProcessingResult:
    """Process a single event group.

    Convenience function that creates a processor and runs it.

    Args:
        db_factory: Factory function returning database connection
        group_id: Group ID to process
        dispatcharr_client: Optional DispatcharrClient
        target_date: Target date (defaults to today)

    Returns:
        ProcessingResult
    """
    processor = EventGroupProcessor(
        db_factory=db_factory,
        dispatcharr_client=dispatcharr_client,
    )
    return processor.process_group(group_id, target_date)


def process_all_event_groups(
    db_factory: Any,
    dispatcharr_client: Any = None,
    target_date: date | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    generation: int | None = None,
) -> BatchProcessingResult:
    """Process all active event groups.

    Convenience function that creates a processor and runs it.

    Args:
        db_factory: Factory function returning database connection
        dispatcharr_client: Optional DispatcharrClient
        target_date: Target date (defaults to today)
        progress_callback: Optional callback(current, total, group_name)
        generation: Cache generation counter (shared across all groups in run)

    Returns:
        BatchProcessingResult
    """
    processor = EventGroupProcessor(
        db_factory=db_factory,
        dispatcharr_client=dispatcharr_client,
    )
    return processor.process_all_groups(
        target_date, progress_callback=progress_callback, generation=generation
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
