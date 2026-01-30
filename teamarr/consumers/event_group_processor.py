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
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from sqlite3 import Connection
from typing import Any

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
# Configurable via ESPN_MAX_WORKERS for users with DNS throttling (PiHole, AdGuard)
MAX_WORKERS = int(os.environ.get("ESPN_MAX_WORKERS", 100))


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
    filtered_stale: int = 0  # Marked as stale in Dispatcharr
    filtered_not_event: int = 0  # Didn't look like an event (no vs/@/at/date)
    filtered_include_regex: int = 0  # Didn't match include pattern
    filtered_exclude_regex: int = 0  # Matched exclude pattern
    filtered_team: int = 0  # Team not in include/exclude filter

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
                "filtered_stale": self.filtered_stale,
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
    filtered_stale: int = 0
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
            "filtered_stale": self.filtered_stale,
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

        # Shared events cache for cross-group reuse in a single generation run
        # Keys are "league:date" strings, values are (events, was_cache_only) tuples
        # was_cache_only=True means the result came from a cache-only lookup (no API call attempted)
        # This avoids redundant API/cache lookups when multiple groups search the same leagues
        # while ensuring groups that need fresh API data can still get it
        self._shared_events: dict[str, tuple[list[Event], bool]] = {}

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
                    logger.warning(
                        "[EVENT_EPG] Preview: M3U refresh error: %s - continuing anyway", e
                    )

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
            result.filtered_stale = filter_result.filtered_stale
            # Combine all built-in eligibility filters into filtered_not_event
            result.filtered_not_event = (
                filter_result.filtered_not_event
                + filter_result.filtered_placeholder
                + filter_result.filtered_unsupported_sport
            )
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

            # Sort: matched first, then unmatched; within each, natural sort by name
            from teamarr.api.routes import natural_sort_key

            result.streams.sort(
                key=lambda s: (not s.matched, natural_sort_key(s.stream_name)),
            )

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

        # Clear shared events cache at start of new generation run
        # This ensures fresh data and allows cross-group reuse within this run
        self._shared_events.clear()

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
                        processed_count,
                        total_groups,
                        f"Loading {group.name}... ({leagues_count} leagues)",
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

                # Create status callback for post-matching phases
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
                        processed_count, total_groups, f"Loading {group.name}... (child group)"
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

                # Create status callback for prefetch progress
                status_cb = None
                if progress_callback:
                    grp_idx = processed_count + 1

                    def make_status_cb(grp_name: str, idx: int):
                        def cb(msg: str):
                            progress_callback(idx, total_groups, f"{grp_name}: {msg}")

                        return cb

                    status_cb = make_status_cb(group.name, grp_idx)

                result = self._process_child_group_internal(
                    conn,
                    group,
                    target_date,
                    stream_progress_callback=stream_cb,
                    status_callback=status_cb,
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
                        processed_count,
                        total_groups,
                        f"Loading {group.name}... ({leagues_count} leagues)",
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

                # Create status callback for post-matching phases
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
                    from teamarr.database.settings import get_display_settings

                    display_settings = get_display_settings(conn)
                    batch_result.total_xmltv = merge_xmltv_content(
                        xmltv_contents,
                        generator_name=display_settings.xmltv_generator_name,
                        generator_url=display_settings.xmltv_generator_url,
                    )
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
        status_callback: Callable[[str], None] | None = None,
    ) -> ProcessingResult:
        """Process a child group - adds streams to parent's channels.

        Child groups don't create their own channels or generate XMLTV.
        They match streams and add them to their parent's existing channels.

        Args:
            conn: Database connection
            group: Child group to process
            target_date: Target date
            stream_progress_callback: Optional callback(current, total, stream_name, matched)
            status_callback: Optional callback for status updates

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
                result.completed_at = datetime.now()
                stats_run.complete(status="completed", error="All streams filtered")
                save_run(conn, stats_run)
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
                    filtered_stale=result.filtered_stale,
                    filtered_include_regex=result.filtered_include_regex,
                    filtered_exclude_regex=result.filtered_exclude_regex,
                    failed_count=result.streams_after_filter,  # All unmatched due to no events
                    filtered_not_event=result.filtered_not_event,
                    total_stream_count=result.streams_fetched,
                )
                return result

            # Step 3: Match streams to events
            match_result = self._match_streams(
                streams,
                group,
                target_date,
                stream_progress_callback=stream_progress_callback,
                status_callback=status_callback,
                resolved_leagues=leagues,  # Pass inherited leagues for proper filtering
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

            # Build event lookup BEFORE team filtering (for cleanup)
            # Use segment-aware event_id to match channel.event_id storage
            def _effective_event_id(m):
                event = m.get("event")
                if not event or not hasattr(event, "id"):
                    return None
                segment = m.get("segment")
                return f"{event.id}-{segment}" if segment else event.id

            all_matched_events = {
                _effective_event_id(m): m.get("event")
                for m in matched_streams
                if _effective_event_id(m)
            }

            # Apply team include/exclude filtering (inherits from parent if not set)
            matched_streams, filtered_team_count = self._filter_by_teams(
                matched_streams, group, conn
            )
            result.filtered_team = filtered_team_count

            # Build set of event IDs that passed the filter (segment-aware)
            passed_event_ids = {
                _effective_event_id(m) for m in matched_streams if _effective_event_id(m)
            }

            # Cleanup existing channels that no longer pass team filter
            # Note: Child groups add streams to PARENT channels, so cleanup here
            # removes channels that were created for events now excluded.
            cleanup_count = self._cleanup_team_filtered_channels(
                group, conn, all_matched_events, passed_event_ids
            )
            if cleanup_count > 0:
                result.channels_deleted = cleanup_count
                logger.info(
                    "[EVENT_EPG] Child group cleanup: %d channels due to team filter",
                    cleanup_count,
                )

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
                logger.info(
                    "[EVENT_EPG] Keyword enforcement moved %d streams", keyword_result.moved_count
                )
        except Exception as e:
            logger.warning("[EVENT_EPG] Keyword enforcement failed: %s", e)

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
                logger.warning("[EVENT_EPG] Cross-group consolidation failed: %s", e)

        # 3. Keyword ordering: ensure main channel has lower number than keyword channels
        try:
            ordering_enforcer = KeywordOrderingEnforcer(self._db_factory, channel_manager)
            ordering_result = ordering_enforcer.enforce()
            if ordering_result.reordered_count > 0:
                logger.info(
                    f"Keyword ordering: reordered {ordering_result.reordered_count} channel pair(s)"
                )
        except Exception as e:
            logger.warning("[EVENT_EPG] Keyword ordering failed: %s", e)

        # 4. Orphan cleanup: delete Dispatcharr channels not tracked in DB
        if lifecycle_service:
            try:
                orphan_result = lifecycle_service.cleanup_orphan_dispatcharr_channels()
                if orphan_result.get("deleted", 0) > 0:
                    logger.info(
                        f"Orphan cleanup: deleted {orphan_result['deleted']} Dispatcharr channels"
                    )
            except Exception as e:
                logger.warning("[EVENT_EPG] Orphan cleanup failed: %s", e)

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
                logger.warning("[EVENT_EPG] Disabled group cleanup failed: %s", e)

    def _process_group_internal(
        self,
        conn: Connection,
        group: EventEPGGroup,
        target_date: date,
        stream_progress_callback: Callable | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> ProcessingResult:
        """Internal processing for a single group.

        Args:
            conn: Database connection
            group: Event group to process
            target_date: Target date for matching
            stream_progress_callback: Optional callback(current, total, stream_name, matched)
            status_callback: Optional callback(status_message) for phase updates
        """
        result = ProcessingResult(group_id=group.id, group_name=group.name)

        # Template is required - skip groups without one
        if group.template_id is None:
            logger.warning(
                "[EVENT_GROUP_SKIP] Group '%s' (id=%d): no template assigned - "
                "template is required for channel naming. Skipping group.",
                group.name,
                group.id,
            )
            result.errors.append("No template assigned - template is required for channel naming")
            result.completed_at = datetime.now()
            return result

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
                result.completed_at = datetime.now()
                stats_run.complete(status="completed", error="All streams filtered")
                save_run(conn, stats_run)
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
                    filtered_stale=filter_result.filtered_stale,
                    filtered_include_regex=filter_result.filtered_include,
                    filtered_exclude_regex=filter_result.filtered_exclude,
                    failed_count=result.streams_after_filter,  # All unmatched due to no events
                    filtered_not_event=filter_result.filtered_not_event,
                    total_stream_count=result.streams_fetched,
                )
                return result

            # Step 3: Match streams to events (uses fingerprint cache)
            match_result = self._match_streams(
                streams,
                group,
                target_date,
                stream_progress_callback=stream_progress_callback,
                status_callback=status_callback,
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

            # Sort channels based on global channel numbering sort_by setting
            from teamarr.database.settings import get_channel_numbering_settings

            channel_numbering = get_channel_numbering_settings(conn)
            matched_streams = self._sort_matched_streams(matched_streams, channel_numbering.sort_by)

            # Enrich ALL matched events with fresh status from provider
            # This ensures lifecycle filtering uses current final status
            matched_streams = self._enrich_matched_events(matched_streams)

            # Build event lookup BEFORE team filtering (for cleanup of existing channels)
            # Use segment-aware event_id to match channel.event_id storage
            def _effective_event_id(m):
                event = m.get("event")
                if not event or not hasattr(event, "id"):
                    return None
                segment = m.get("segment")
                return f"{event.id}-{segment}" if segment else event.id

            all_matched_events = {
                _effective_event_id(m): m.get("event")
                for m in matched_streams
                if _effective_event_id(m)
            }

            # Apply team include/exclude filtering
            matched_streams, filtered_team_count = self._filter_by_teams(
                matched_streams, group, conn
            )
            result.filtered_team = filtered_team_count

            # Build set of event IDs that passed the filter (segment-aware)
            passed_event_ids = {
                _effective_event_id(m) for m in matched_streams if _effective_event_id(m)
            }

            # Cleanup existing channels that no longer pass team filter
            # (handles both include and exclude modes, global and per-group)
            cleanup_count = self._cleanup_team_filtered_channels(
                group, conn, all_matched_events, passed_event_ids
            )
            if cleanup_count > 0:
                result.channels_deleted = cleanup_count
                logger.info("[EVENT_EPG] Cleaned up %d channels due to team filter", cleanup_count)

            # Build stream dict for cleanup (fingerprint-based content change detection)
            current_streams = {s.get("id"): s for s in streams if s.get("id")}

            if matched_streams:
                if status_callback:
                    status_callback(f"Processing {len(matched_streams)} channels...")
                lifecycle_result = self._process_channels(
                    matched_streams, group, conn, current_streams=current_streams
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

                stats_run.programmes_total = programmes_total
                stats_run.programmes_events = event_programmes
                stats_run.programmes_pregame = pregame
                stats_run.programmes_postgame = postgame
                stats_run.xmltv_size_bytes = result.xmltv_size

                # Step 6: Store XMLTV for this group (in database)
                # Always store, even if empty - this clears stale XMLTV when no events match
                self._store_group_xmltv(conn, group.id, xmltv_content or "")

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
            logger.warning("[EVENT_EPG] Dispatcharr not configured - cannot fetch streams")
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
                    "is_stale": s.is_stale,
                }
                for s in streams
            ]
            # Sort by stream ID ascending for consistent processing order
            stream_dicts.sort(key=lambda s: s["id"])
            return stream_dicts

        except Exception as e:
            logger.error("[EVENT_EPG] Failed to fetch streams: %s", e)
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
            result.filtered_stale
            + result.filtered_placeholder
            + result.filtered_unsupported_sport
            + result.filtered_not_event
            + result.filtered_include
            + result.filtered_exclude
        )
        if filtered_total > 0:
            logger.info(
                "[FILTER] Group '%s': %d input → %d passed "
                "(stale: -%d, placeholder: -%d, unsupported_sport: -%d, not_event: -%d, "
                "include: -%d, exclude: -%d)",
                group.name,
                result.total_input,
                result.passed_count,
                result.filtered_stale,
                result.filtered_placeholder,
                result.filtered_unsupported_sport,
                result.filtered_not_event,
                result.filtered_include,
                result.filtered_exclude,
            )

        return result.passed, result

    def _get_all_known_leagues(self) -> list[str]:
        """Get all known leagues from the league cache.

        Returns ALL leagues discovered from providers (ESPN, TSDB, etc.),
        not just the import-enabled leagues in the leagues table.
        This allows matching against any league for multi-sport groups.
        """
        with self._db_factory() as conn:
            cursor = conn.execute("SELECT league_slug FROM league_cache")
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
            days_ahead = (
                row["event_match_days_ahead"] if row and row["event_match_days_ahead"] else 3
            )

        # Build date range: [target - days_back, target + days_ahead]
        dates_to_fetch = [
            target_date + timedelta(days=offset) for offset in range(-days_back, days_ahead + 1)
        ]
        logger.debug(
            "[EVENT_EPG] Fetching events from %s to %s (%d days)",
            dates_to_fetch[0],
            dates_to_fetch[-1],
            len(dates_to_fetch),
        )

        def fetch_league_events(league: str, fetch_date: date) -> tuple[str, date, list[Event]]:
            """Fetch events for a single league/date (for parallel execution)."""
            try:
                # TSDB leagues: cache-only (don't hit API during EPG generation)
                # TSDB cache builds organically from startup/scheduled refresh
                is_tsdb = self._service.get_provider_name(league) == "tsdb"
                events = self._service.get_events(league, fetch_date, cache_only=is_tsdb)
                return (league, fetch_date, events)
            except Exception as e:
                logger.warning(
                    "[EVENT_EPG] Failed to fetch events for %s on %s: %s", league, fetch_date, e
                )
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
                    logger.warning(
                        "[EVENT_EPG] Failed to fetch events for %s on %s: %s", league, fetch_date, e
                    )

        return all_events

    def _match_streams(
        self,
        streams: list[dict],
        group: EventEPGGroup,
        target_date: date,
        stream_progress_callback: Callable | None = None,
        status_callback: Callable[[str], None] | None = None,
        resolved_leagues: list[str] | None = None,
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
            status_callback: Optional callback(status_message) for status updates
            resolved_leagues: Pre-resolved leagues (for child groups inheriting from parent)
        """
        # Get ALL known leagues from the cache to search against
        # This includes all leagues discovered from ESPN/TSDB (287+), not just
        # the 64 import-enabled leagues in the leagues table
        search_leagues = self._get_all_known_leagues()

        # Load settings for event filtering
        with self._db_factory() as conn:
            row = conn.execute("SELECT include_final_events FROM settings WHERE id = 1").fetchone()
            include_final_events = bool(row["include_final_events"]) if row else False

        sport_durations = self._load_sport_durations_cached()

        # Use resolved_leagues if provided (e.g., inherited from parent), else group.leagues
        include_leagues = resolved_leagues if resolved_leagues else group.leagues

        matcher = StreamMatcher(
            service=self._service,
            db_factory=self._db_factory,
            group_id=group.id,
            search_leagues=search_leagues,  # Search ALL leagues + group's leagues
            include_leagues=include_leagues,  # Filter to resolved/configured leagues
            include_final_events=include_final_events,
            sport_durations=sport_durations,
            generation=getattr(self, "_generation", None),  # Use shared generation if set
            custom_regex_teams=group.custom_regex_teams,
            custom_regex_teams_enabled=group.custom_regex_teams_enabled,
            custom_regex_date=group.custom_regex_date,
            custom_regex_date_enabled=group.custom_regex_date_enabled,
            custom_regex_time=group.custom_regex_time,
            custom_regex_time_enabled=group.custom_regex_time_enabled,
            custom_regex_league=group.custom_regex_league,
            custom_regex_league_enabled=group.custom_regex_league_enabled,
            shared_events=self._shared_events,  # Reuse events across groups in same run
            stream_timezone=group.stream_timezone,  # TZ for interpreting stream dates
        )

        result = matcher.match_all(
            streams,
            target_date,
            progress_callback=stream_progress_callback,
            status_callback=status_callback,
        )

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
        Also applies UFC segment expansion to create separate channels per segment.
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
                            "card_segment": result.card_segment,  # UFC segment from classifier
                        }
                    )

        # Apply UFC segment expansion
        # This splits UFC streams into separate segment channels
        matched = self._expand_ufc_segments(matched)

        return matched

    def _expand_ufc_segments(self, matched_streams: list[dict]) -> list[dict]:
        """Expand UFC streams into segment-based channels.

        Groups UFC streams by detected segment (early_prelims, prelims, main_card)
        and creates separate channel entries for each. Non-UFC streams pass through.

        Args:
            matched_streams: List of {'stream': ..., 'event': ...} dicts

        Returns:
            Expanded list with UFC streams grouped by segment
        """
        from teamarr.consumers.ufc_segments import expand_ufc_segments

        sport_durations = self._load_sport_durations_cached()
        return expand_ufc_segments(matched_streams, sport_durations)

    def _enrich_matched_events(self, matched_streams: list[dict]) -> list[dict]:
        """Enrich all matched events with fresh status from provider.

        Fetches fresh event data from summary endpoint for each matched event.
        This ensures lifecycle filtering uses current final status, not stale
        cached status from scoreboard/schedule.

        Args:
            matched_streams: List of {'stream': ..., 'event': ...} dicts

        Returns:
            Same list with events replaced by enriched versions
        """
        if not matched_streams:
            return matched_streams

        enriched = []
        for match in matched_streams:
            event = match.get("event")
            if event:
                old_status = event.status.state if event.status else "N/A"
                # Refresh event status from provider (invalidates cache, fetches fresh)
                refreshed = self._service.refresh_event_status(event)
                new_status = refreshed.status.state if refreshed.status else "N/A"
                if old_status != new_status:
                    logger.debug(
                        "[ENRICH] event=%s status changed: %s → %s",
                        event.id,
                        old_status,
                        new_status,
                    )
                # Preserve all keys (including segment info for UFC)
                enriched_match = dict(match)
                enriched_match["event"] = refreshed
                enriched.append(enriched_match)
            else:
                enriched.append(match)

        logger.debug("[EVENT_EPG] Enriched %d matched events with fresh status", len(enriched))
        return enriched

    def _filter_by_teams(
        self,
        matched_streams: list[dict],
        group: "EventEPGGroup",
        conn,
    ) -> tuple[list[dict], int]:
        """Filter matched streams by team include/exclude configuration.

        Uses canonical team selection (provider, team_id) for unambiguous matching.
        Filter is set on parent groups and inherited by children.

        Args:
            matched_streams: List of {'stream': ..., 'event': ...} dicts
            group: The event group being processed
            conn: Database connection for parent lookup

        Returns:
            Tuple of (filtered_streams, filtered_count)
        """
        # Get effective team filter (from group or parent)
        include_teams, exclude_teams, mode = self._get_effective_team_filter(group, conn)

        # No filter configured
        if not include_teams and not exclude_teams:
            return matched_streams, 0

        filter_list = include_teams if include_teams else exclude_teams
        filtered = []
        filtered_count = 0

        # Extract leagues that have teams in the filter
        # Only filter events from leagues with explicit selections
        filter_leagues = {f.get("league") for f in filter_list if f.get("league")}

        for match in matched_streams:
            event = match.get("event")
            if not event:
                # No event - can't filter by team, keep it
                filtered.append(match)
                continue

            # Get event's league
            event_league = event.league if event else None

            # If no teams from this league are in the filter, pass through unfiltered
            if event_league and event_league not in filter_leagues:
                filtered.append(match)
                continue

            # Check if either team matches filter
            home_match = self._team_matches_filter(event.home_team, filter_list)
            away_match = self._team_matches_filter(event.away_team, filter_list)
            team_in_filter = home_match or away_match

            if mode == "include":
                # Include mode: keep if team IS in list
                if team_in_filter:
                    filtered.append(match)
                else:
                    filtered_count += 1
                    logger.debug(
                        f"Team filter excluded: {event.name} - "
                        f"neither {event.home_team.name if event.home_team else 'N/A'} "
                        f"nor {event.away_team.name if event.away_team else 'N/A'} in include list"
                    )
            else:
                # Exclude mode: keep if team is NOT in list
                if not team_in_filter:
                    filtered.append(match)
                else:
                    filtered_count += 1
                    logger.debug(f"Team filter excluded: {event.name} - team in exclude list")

        if filtered_count > 0:
            logger.info(
                "[EVENT_EPG] Team filter: %d streams excluded, %d remaining",
                filtered_count,
                len(filtered),
            )

        return filtered, filtered_count

    def _get_effective_team_filter(
        self,
        group: "EventEPGGroup",
        conn,
    ) -> tuple[list[dict] | None, list[dict] | None, str]:
        """Get team filter, inheriting from parent if needed, with settings fallback.

        Priority chain:
        1. Group's own filter (if configured)
        2. Parent group's filter (if child and parent has filter)
        3. Global settings default (if configured)
        4. No filtering (default)

        Returns:
            Tuple of (include_teams, exclude_teams, mode)
        """
        from teamarr.database.groups import get_group
        from teamarr.database.settings import get_team_filter_settings

        # If group has its own filter, use it
        if group.include_teams or group.exclude_teams:
            return group.include_teams, group.exclude_teams, group.team_filter_mode

        # Otherwise inherit from parent
        if group.parent_group_id:
            parent = get_group(conn, group.parent_group_id)
            if parent and (parent.include_teams or parent.exclude_teams):
                return parent.include_teams, parent.exclude_teams, parent.team_filter_mode

        # Fall back to global settings default
        settings = get_team_filter_settings(conn)
        if settings.include_teams or settings.exclude_teams:
            return settings.include_teams, settings.exclude_teams, settings.mode

        return None, None, "include"

    def _team_matches_filter(
        self,
        team,
        filter_teams: list[dict],
    ) -> bool:
        """Check if a team matches any entry in the filter list.

        Matches on provider + team_id. League is optional (some teams
        play in multiple leagues).

        Args:
            team: Team object from event
            filter_teams: List of filter entries with provider, team_id, league

        Returns:
            True if team matches any filter entry
        """
        if not team or not filter_teams:
            return False

        for f in filter_teams:
            if f.get("provider") == team.provider and f.get("team_id") == team.id:
                # League check is optional
                filter_league = f.get("league")
                if filter_league:
                    if filter_league == team.league:
                        return True
                else:
                    return True
        return False

    def _cleanup_team_filtered_channels(
        self,
        group: "EventEPGGroup",
        conn: Connection,
        all_matched_events: dict[str, "Event"],
        passed_event_ids: set[str],
    ) -> int:
        """Delete existing channels that don't pass team filter.

        When teams are added to exclude list (or removed from include list),
        existing channels for those teams should be deleted.

        This handles both include and exclude modes:
        - Include mode: channels for teams NOT in include list are deleted
        - Exclude mode: channels for teams IN exclude list are deleted

        Works for both global and per-group team filters.

        Args:
            group: The event group
            conn: Database connection
            all_matched_events: Dict of event_id -> Event for all matched events
                               (before team filtering was applied)
            passed_event_ids: Set of event IDs that passed the team filter

        Returns:
            Number of channels deleted
        """
        from teamarr.database.channels import get_managed_channels_for_group

        # Get effective team filter (group -> parent -> global)
        include_teams, exclude_teams, mode = self._get_effective_team_filter(group, conn)

        if not include_teams and not exclude_teams:
            return 0  # No filter configured

        # Get all existing channels for this group
        channels = get_managed_channels_for_group(conn, group.id)

        deleted_count = 0
        for channel in channels:
            event_id = channel.event_id

            # Only process channels whose events were matched in this run
            # (meaning the event is for today's date and we have the team info)
            if event_id not in all_matched_events:
                continue

            # If the event passed the filter, keep the channel
            if event_id in passed_event_ids:
                continue

            # Event was matched but didn't pass filter - delete the channel
            success = self._delete_channel_for_team_filter(conn, channel, reason="team_filter")
            if success:
                deleted_count += 1
                logger.info(
                    "[EVENT_EPG] Deleted channel '%s' (event_id=%s) - team excluded by filter",
                    channel.channel_name,
                    event_id,
                )

        return deleted_count

    def _delete_channel_for_team_filter(
        self,
        conn: Connection,
        channel,
        reason: str,
    ) -> bool:
        """Delete a managed channel due to team filter.

        Args:
            conn: Database connection
            channel: ManagedChannel to delete
            reason: Deletion reason

        Returns:
            True if deleted successfully
        """
        from teamarr.database.channels import (
            log_channel_history,
            mark_channel_deleted,
        )

        try:
            # Soft delete in our database
            mark_channel_deleted(conn, channel.id, reason=reason)

            # Log the history
            log_channel_history(
                conn=conn,
                managed_channel_id=channel.id,
                change_type="deleted",
                change_source="team_filter",
                notes=f"Channel deleted: {reason}",
            )

            # Delete from Dispatcharr if connected
            if self._dispatcharr_client and channel.dispatcharr_channel_id:
                try:
                    lifecycle_service = create_lifecycle_service(
                        self._db_factory,
                        self._service,
                        self._dispatcharr_client,
                    )
                    if lifecycle_service._channel_manager:
                        lifecycle_service._channel_manager.delete_channel(
                            channel.dispatcharr_channel_id
                        )
                except Exception as e:
                    logger.warning(
                        "[EVENT_EPG] Failed to delete channel %d from Dispatcharr: %s",
                        channel.dispatcharr_channel_id,
                        e,
                    )

            return True
        except Exception as e:
            logger.error(
                "[EVENT_EPG] Failed to delete channel %d for team filter: %s",
                channel.id,
                e,
            )
            return False

    def _sort_matched_streams(
        self,
        matched_streams: list[dict],
        sort_order: str,
    ) -> list[dict]:
        """Sort matched streams based on global sort_by setting.

        Sort orders:
        - 'time': Sort by event start time (default)
        - 'sport_league_time': Sort by sport, then league, then start time
        - 'stream_order': Keep original stream order (no sorting)
        - 'sport_time': Legacy - sort by sport, then start time
        - 'league_time': Legacy - sort by league, then start time

        Args:
            matched_streams: List of {'stream': ..., 'event': ...} dicts
            sort_order: One of 'time', 'sport_league_time', 'stream_order'

        Returns:
            Sorted list of matched streams
        """
        if not matched_streams:
            return matched_streams

        # stream_order = keep original order, no sorting
        if sort_order == "stream_order":
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

        if sort_order == "sport_league_time":
            # Sort by sport, then league, then by start time
            def sort_key(m: dict):
                event = m.get("event")
                sport = event.sport.lower() if event and event.sport else "zzz"
                league = event.league.lower() if event and event.league else "zzz"
                return (sport, league, get_start_time(m))

            return sorted(matched_streams, key=sort_key)

        elif sort_order == "sport_time":
            # Legacy: Sort by sport (alphabetically), then by start time
            def sort_key(m: dict):
                event = m.get("event")
                sport = event.sport.lower() if event and event.sport else "zzz"
                return (sport, get_start_time(m))

            return sorted(matched_streams, key=sort_key)

        elif sort_order == "league_time":
            # Legacy: Sort by league (alphabetically), then by start time
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
                origin_method = getattr(result, "origin_match_method", None)
                if origin_method and hasattr(origin_method, "value"):
                    origin_method = origin_method.value  # Convert enum to string

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
                        origin_match_method=origin_method,
                    )
                )
            elif result.matched and not result.included:
                # Matched but excluded (wrong league) - still counts as a match
                event_date = None
                if result.event and result.event.start_time:
                    event_date = result.event.start_time.strftime("%Y-%m-%d %H:%M")
                match_method = getattr(result, "match_method", None)
                if match_method and hasattr(match_method, "value"):
                    match_method = match_method.value  # Convert enum to string
                confidence = getattr(result, "confidence", None)
                origin_method = getattr(result, "origin_match_method", None)
                if origin_method and hasattr(origin_method, "value"):
                    origin_method = origin_method.value  # Convert enum to string

                matched_list.append(
                    MatchedStream(
                        run_id=run_id,
                        group_id=group_id,
                        group_name=group_name,
                        stream_id=stream_id,
                        stream_name=result.stream_name,
                        event_id=result.event.id if result.event else "",
                        event_name=result.event.name if result.event else None,
                        event_date=event_date,
                        detected_league=result.league,
                        home_team=result.event.home_team.name
                        if result.event and result.event.home_team
                        else None,
                        away_team=result.event.away_team.name
                        if result.event and result.event.away_team
                        else None,
                        from_cache=getattr(result, "from_cache", False),
                        excluded=True,
                        exclusion_reason=result.exclusion_reason or "excluded_league",
                        match_method=match_method,
                        confidence=confidence,
                        origin_match_method=origin_method,
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
                # Skip filtered streams (placeholder, sport_not_supported, etc.)
                # These are expected exclusions, not match failures
                if result.exclusion_reason and result.exclusion_reason.startswith(
                    ("placeholder", "sport_not_supported")
                ):
                    continue

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
            logger.debug(
                "[EVENT_EPG] Saved %d matched streams for group %s", len(matched_list), group_name
            )

        if failed_list:
            save_failed_matches(conn, failed_list)
            logger.debug(
                "[EVENT_EPG] Saved %d failed matches for group %s", len(failed_list), group_name
            )

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
            "channel_group_mode": group.channel_group_mode,  # "static", "sport", or "league"
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
            logger.debug("[EVENT_EPG] Error in global AUTO reassignment: %s", e)

        # V1 Parity Step 1: Process scheduled deletions first
        try:
            deletion_result = lifecycle_service.process_scheduled_deletions()
            combined_result.merge(deletion_result)
            if deletion_result.deleted:
                logger.info("[EVENT_EPG] Deleted %d expired channels", len(deletion_result.deleted))
        except Exception as e:
            logger.debug("[EVENT_EPG] Error processing scheduled deletions: %s", e)

        # V1 Parity Step 2: Cleanup deleted/missing/changed streams
        if current_streams is not None:
            try:
                cleanup_result = lifecycle_service.cleanup_deleted_streams(
                    group.id, current_streams
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

        # V1 Parity Step 5: Reassign channel numbers to compact range
        try:
            reassign_result = lifecycle_service.reassign_group_channels(group.id)
            if reassign_result.get("reassigned"):
                logger.info(
                    f"Reassigned {len(reassign_result['reassigned'])} channels in group {group.id}"
                )
        except Exception as e:
            logger.debug("[EVENT_EPG] Error reassigning channel numbers: %s", e)

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
            logger.warning("[EVENT_EPG] Template %s not found", template_id)
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

        # Load sport durations and lookback from settings
        options.sport_durations = self._load_sport_durations(conn)
        lookback_hours = self._load_lookback_hours(conn)

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
                matched_streams,
                filler_config,
                options.sport_durations,
                lookback_hours,
                prepend_postponed_label=options.prepend_postponed_label,
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
        """Load sport duration settings from database.

        Dynamically loads all sports from DurationSettings dataclass.
        """
        from teamarr.database.settings import get_all_settings

        all_settings = get_all_settings(conn)
        return asdict(all_settings.durations)

    def _load_lookback_hours(self, conn: Connection) -> int:
        """Load EPG lookback hours setting from database."""
        row = conn.execute("SELECT epg_lookback_hours FROM settings WHERE id = 1").fetchone()
        if not row:
            return 6  # Default
        return row[0] or 6

    def _generate_filler_for_streams(
        self,
        matched_streams: list[dict],
        filler_config: EventFillerConfig,
        sport_durations: dict[str, float],
        lookback_hours: int = 6,
        prepend_postponed_label: bool = True,
    ) -> EventFillerResult:
        """Generate filler programmes for matched event streams.

        Args:
            matched_streams: List of matched stream/event dicts
            filler_config: Filler configuration from template
            sport_durations: Sport duration settings
            lookback_hours: How far back to generate EPG (for preceding content)
            prepend_postponed_label: Whether to prepend "Postponed: " for postponed events

        Returns:
            EventFillerResult with programmes and pregame/postgame counts
        """
        from teamarr.config import get_user_timezone

        filler_generator = EventFillerGenerator(self._service)
        result = EventFillerResult()

        # Get configured timezone
        tz = get_user_timezone()

        # Build filler options - lookback allows preceding EPG content
        now = datetime.now(tz)
        epg_start = now - timedelta(hours=lookback_hours)
        options = EventFillerOptions(
            epg_start=epg_start,
            epg_end=now + timedelta(days=1),  # 24 hour window
            epg_timezone=str(tz),
            sport_durations=sport_durations,
            default_duration=3.0,
            postgame_buffer_hours=24.0,
            prepend_postponed_label=prepend_postponed_label,
        )

        for stream_match in matched_streams:
            event = stream_match.get("event")

            if not event:
                continue

            # UFC segment support: extract segment info if present
            segment = stream_match.get("segment")
            segment_start = stream_match.get("segment_start")
            segment_end = stream_match.get("segment_end")

            # Use consistent tvg_id matching EventEPGGenerator and ChannelLifecycleService
            from teamarr.consumers.lifecycle import generate_event_tvg_id

            channel_id = generate_event_tvg_id(event.id, event.provider, segment)

            # For UFC segments, override event times with segment-specific times
            if segment_start and segment_end:
                segment_options = EventFillerOptions(
                    epg_start=epg_start,
                    epg_end=segment_end + timedelta(hours=24),
                    epg_timezone=str(tz),
                    sport_durations=sport_durations,
                    default_duration=3.0,
                    postgame_buffer_hours=24.0,
                    event_end_override=segment_end,  # Use exact segment end time
                    prepend_postponed_label=prepend_postponed_label,
                )
                # Create a modified event with segment start time
                from dataclasses import replace

                segment_event = replace(event, start_time=segment_start)
                use_event = segment_event
                use_options = segment_options
            else:
                use_event = event
                use_options = options

            try:
                filler_result = filler_generator.generate_with_counts(
                    event=use_event,
                    channel_id=channel_id,
                    config=filler_config,
                    options=use_options,
                )
                result.programmes.extend(filler_result.programmes)
                result.pregame_count += filler_result.pregame_count
                result.postgame_count += filler_result.postgame_count
            except Exception as e:
                logger.warning(
                    "[EVENT_EPG] Failed to generate filler for event %s: %s", event.id, e
                )

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
        logger.debug("[EVENT_EPG] Stored XMLTV for group %d", group_id)

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
                logger.debug("[EVENT_EPG] No Dispatcharr EPG source configured - skipping refresh")
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
                logger.warning("[EVENT_EPG] EPG refresh failed: %s", result.message)

        except Exception as e:
            logger.warning("[EVENT_EPG] Error during EPG refresh: %s", e)

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
                    logger.debug("[EVENT_EPG] No managed channels to associate EPG with")
                    return

                # Build EPG data lookup from Dispatcharr
                epg_lookup = channel_manager.build_epg_lookup(epg_source_id)

                if not epg_lookup:
                    logger.debug("[EVENT_EPG] No EPG data found in Dispatcharr to associate")
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
                    logger.info("[EVENT_EPG] Associated EPG data with %d channels", associated)
                if not_found:
                    logger.debug(
                        "[EVENT_EPG] EPG data not found for %d channels (pending refresh)",
                        not_found,
                    )

        except Exception as e:
            logger.warning("[EVENT_EPG] Error associating EPG with channels: %s", e)


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
    service: SportsDataService | None = None,
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

    Returns:
        BatchProcessingResult
    """
    processor = EventGroupProcessor(
        db_factory=db_factory,
        dispatcharr_client=dispatcharr_client,
        service=service,
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
