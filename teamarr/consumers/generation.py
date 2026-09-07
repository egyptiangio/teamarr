"""Unified EPG generation workflow.

This module provides the single source of truth for EPG generation.
Both the streaming API endpoint and the background scheduler call this.
"""

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from teamarr.channelsdvr.client import ChannelsDVRClient
from teamarr.dispatcharr import EPGManager, M3UManager
from teamarr.dispatcharr.factory import DispatcharrConnection
from teamarr.dispatcharr.managers import ChannelManager
from teamarr.emby.client import EmbyClient
from teamarr.jellyfin.client import JellyfinClient
from teamarr.services import create_default_service
from teamarr.services.sports_data import flush_shared_cache
from teamarr.services.stream_ordering import StreamOrderingService
from teamarr.utilities import call_metrics
from teamarr.utilities.xmltv import merge_xmltv_content

logger = logging.getLogger(__name__)


class GenerationCancelled(Exception):
    """Raised when a generation run is cancelled by the user."""


# Concurrency for the stream-order push phase (#735). Bounded well under the
# client's pool so a run cannot monopolize Dispatcharr, and matched to what an
# ordinary self-hosted Django instance answers comfortably.
_ORDERING_PUSH_WORKERS = 8

# Global lock to prevent concurrent EPG generation runs
_generation_lock = threading.Lock()
_generation_running = False


@dataclass
class GenerationResult:
    """Result of a full EPG generation run."""

    success: bool = True
    error: str | None = None

    # Timing
    started_at: float = 0.0
    completed_at: float = 0.0
    duration_seconds: float = 0.0

    # EPG stats
    teams_processed: int = 0
    teams_programmes: int = 0
    groups_processed: int = 0
    groups_programmes: int = 0
    programmes_total: int = 0

    # File output
    file_written: bool = False
    file_path: str | None = None
    file_size: int = 0

    # Sub-task results
    m3u_refresh: dict = field(default_factory=dict)
    stream_ordering: dict = field(default_factory=dict)
    epg_refresh: dict = field(default_factory=dict)
    epg_association: dict = field(default_factory=dict)
    deletions: dict = field(default_factory=dict)
    reconciliation: dict = field(default_factory=dict)
    cleanup: dict = field(default_factory=dict)
    logo_cleanup: dict = field(default_factory=dict)
    channel_conflicts: dict = field(default_factory=dict)
    emby_refresh: dict = field(default_factory=dict)
    jellyfin_refresh: dict = field(default_factory=dict)
    channelsdvr_refresh: dict = field(default_factory=dict)
    channelsdvr_epg_refresh: dict = field(default_factory=dict)
    # One entry per media server refreshed this run (#649): persisted on the
    # run row so a server that fails every run is visible after the fact.
    media_server_outcomes: list[dict] = field(default_factory=list)

    # For stats run tracking
    run_id: int | None = None

    # Wall-clock seconds per generation phase (persisted to run stats so any
    # two runs — local or live — can be compared phase-by-phase).
    phase_timings: dict = field(default_factory=dict)


class _PhaseTimer:
    """Records elapsed wall time between phase marks."""

    def __init__(self, timings: dict):
        self._timings = timings
        self._last = time.time()

    def mark(self, phase: str) -> None:
        now = time.time()
        self._timings[phase] = round(self._timings.get(phase, 0.0) + (now - self._last), 2)
        self._last = now


# Type alias for progress callback
# (phase: str, percent: int, message: str, current: int, total: int, item_name: str) -> None
ProgressCallback = Callable[[str, int, str, int, int, str], None]


def run_full_generation(
    db_factory: Callable[[], Any],
    dispatcharr_client: Any | None = None,
    progress_callback: ProgressCallback | None = None,
    manual: bool = False,
) -> GenerationResult:
    """Run the complete EPG generation workflow.

    This is the single source of truth for EPG generation. Both the
    streaming API endpoint and the background scheduler call this function.

    Workflow:
    1. Refresh M3U accounts (0-5%)
    2. Process all teams (5-50%) - 45% budget
    3. Process all event groups (50-95%) - 45% budget
    4. Merge and save XMLTV (95-96%)
    5. Dispatcharr EPG refresh + channel association (96-98%)
    6. Process scheduled deletions (98-99%)
    7. Run reconciliation + cleanup (99-100%)

    Args:
        db_factory: Factory function returning database connection context manager
        dispatcharr_client: Optional DispatcharrClient for Dispatcharr operations
        progress_callback: Optional callback for progress updates
        manual: True for user-triggered runs (API endpoints); bypasses the
            live-event #1 stream pin (#232) so a wrong pin can be corrected.
            Scheduled runs leave this False.

    Returns:
        GenerationResult with all stats and sub-task results
    """
    global _generation_running

    # Prevent concurrent generation runs
    if not _generation_lock.acquire(blocking=False):
        logger.warning("[GENERATION] Already in progress, skipping duplicate run")
        result = GenerationResult()
        result.success = False
        result.error = "Generation already in progress"
        return result

    if _generation_running:
        _generation_lock.release()
        logger.warning("[GENERATION] Already in progress (flag check), skipping")
        result = GenerationResult()
        result.success = False
        result.error = "Generation already in progress"
        return result

    _generation_running = True

    from teamarr.consumers import (
        create_lifecycle_service,
        create_reconciler,
        detect_stale_groups,
        process_all_event_groups,
        process_all_teams,
    )
    from teamarr.consumers.team_processor import get_all_team_xmltv
    from teamarr.database.channels import get_reconciliation_settings
    from teamarr.database.groups import get_all_group_xmltv
    from teamarr.database.settings import (
        get_dispatcharr_settings,
        get_display_settings,
        get_epg_settings,
    )
    from teamarr.database.stats import create_run

    result = GenerationResult()
    result.started_at = time.time()

    def update_progress(
        phase: str,
        percent: int,
        message: str,
        current: int = 0,
        total: int = 0,
        item_name: str = "",
    ):
        if progress_callback:
            progress_callback(phase, percent, message, current, total, item_name)

    # Create stats run for tracking with database-level lock
    # Use BEGIN IMMEDIATE to acquire exclusive write lock BEFORE checking
    # This prevents race conditions where two processes both pass the check
    # before either has inserted their row
    with db_factory() as conn:
        try:
            # BEGIN IMMEDIATE acquires write lock immediately, blocking other writers
            conn.execute("BEGIN IMMEDIATE")

            # Now check for in-progress runs - with lock held, this is reliable
            recent_running = conn.execute("""
                SELECT id FROM processing_runs
                WHERE run_type = 'full_epg'
                  AND status = 'running'
                  AND started_at > datetime('now', '-5 minutes')
                LIMIT 1
            """).fetchone()

            if recent_running:
                conn.execute("ROLLBACK")
                _generation_running = False
                _generation_lock.release()
                logger.warning(
                    "[GENERATION] Already in progress (run %d), skipping", recent_running["id"]
                )
                result = GenerationResult()
                result.success = False
                result.error = "Generation already in progress"
                return result

            # No running jobs - create our run (still holding lock)
            stats_run = create_run(conn, run_type="full_epg")
            result.run_id = stats_run.id
            # create_run commits, which releases the lock

        except Exception as e:
            try:
                conn.execute("ROLLBACK")
            except Exception as rollback_err:
                logger.debug(
                    "[GENERATION] Rollback failed during lock acquisition: %s", rollback_err
                )
            _generation_running = False
            _generation_lock.release()
            logger.error("[GENERATION] Failed to acquire lock: %s", e)
            result = GenerationResult()
            result.success = False
            result.error = f"Failed to acquire lock: {e}"
            return result

    # Import cancellation helpers
    from teamarr.consumers.generation_status import cancel_generation, is_cancellation_requested

    def check_cancelled():
        """Check if cancellation was requested and raise if so."""
        if is_cancellation_requested():
            raise GenerationCancelled("Cancelled by user")

    try:
        # Increment generation counter ONCE at start of full EPG run
        # This ensures all groups in this run share the same generation
        from teamarr.consumers.stream_match_cache import increment_generation_counter

        current_generation = increment_generation_counter(db_factory)
        logger.info("[GENERATION] Starting with cache generation %d", current_generation)

        # Reset the run-scoped provider-call counter so this run's totals start
        # clean. Runs are serialized (duplicate runs are rejected above), so a
        # single process-global counter is safe. Snapshot is persisted at run end.

        call_metrics.reset()

        # Create a single SportsDataService instance to share across all processing
        # This ensures the event cache stays warm throughout the entire run
        # (Previously each consumer created its own service with a cold cache)
        shared_service = create_default_service()

        # Get settings
        with db_factory() as conn:
            settings = get_epg_settings(conn)
            dispatcharr_settings = get_dispatcharr_settings(conn)
            display_settings = get_display_settings(conn)

        timer = _PhaseTimer(result.phase_timings)

        # Step 1: Refresh M3U accounts (0-5%)
        check_cancelled()
        update_progress("init", 3, "Refreshing M3U accounts...")
        if dispatcharr_client:
            result.m3u_refresh = _refresh_m3u_accounts(db_factory, dispatcharr_client)
        timer.mark("m3u_refresh")

        # Step 2: Process all teams (5-50%) - 45% budget
        check_cancelled()
        update_progress("teams", 5, "Processing teams...")

        teams_start_time = time.time()

        def team_progress(current: int, total: int, name: str):
            # Maps 0-100% within teams to 5-50% overall
            pct = 5 + int((current / total) * 45) if total > 0 else 5
            elapsed = time.time() - teams_start_time
            remaining = total - current

            # Messages from team_processor already include context
            # (Processing X..., Finished X, now processing: Y, Z)
            # Just add timing and counts
            if remaining > 0:
                msg = f"{name} ({current}/{total}) - {remaining} remaining [{elapsed:.1f}s]"
            else:
                msg = f"{name} ({current}/{total}) [{elapsed:.1f}s]"
            update_progress("teams", pct, msg, current, total, name)

        team_result = process_all_teams(db_factory=db_factory, progress_callback=team_progress)
        result.teams_processed = team_result.teams_processed
        result.teams_programmes = team_result.total_programmes
        timer.mark("teams")

        # Transition message - teams done, starting groups
        logger.info("[GENERATION] Sending transition message: teams -> groups")
        update_progress(
            "groups",
            50,
            f"Teams complete ({result.teams_processed} processed), loading event groups...",
            0,
            1,
            "Loading event groups...",
        )
        logger.info("[GENERATION] Transition message sent")

        # Step 3: Process all event groups (50-95%) - 45% budget
        check_cancelled()

        groups_start_time = time.time()

        def group_progress(current: int, total: int, name: str):
            # Maps 0-100% within groups to 50-95% overall
            pct = 50 + int((current / total) * 45) if total > 0 else 50
            elapsed = time.time() - groups_start_time

            # Check if this is a stream-level progress update (contains ✓ or ✗)
            if "✓" in name or "✗" in name:
                # Stream-level progress - name contains "GroupName: StreamName ✓/✗ (x/y)"
                # Pass the full message as item_name for display in toast
                update_progress("groups", pct, name, current, total, name)
            else:
                # Group completion - add context
                remaining = total - current
                if remaining > 0:
                    msg = f"Finished {name} ({current}/{total}) - {remaining} remaining [{elapsed:.1f}s]"  # noqa: E501
                else:
                    msg = f"Finished {name} ({current}/{total}) [{elapsed:.1f}s]"
                update_progress("groups", pct, msg, current, total, name)

        # Compute external occupied channel numbers once for the entire run (#146)
        # This prevents Teamarr from assigning numbers already used by non-Teamarr channels
        from teamarr.consumers.lifecycle import compute_external_occupied

        _channel_mgr = (
            dispatcharr_client.channels
            if isinstance(dispatcharr_client, DispatcharrConnection)
            else None
        )
        external_occupied = compute_external_occupied(db_factory, _channel_mgr)

        # Pre-generation validation: detect channel range conflicts (#146)
        if external_occupied:
            result.channel_conflicts = _validate_channel_ranges(
                db_factory, external_occupied
            )

        group_result = process_all_event_groups(
            db_factory=db_factory,
            dispatcharr_client=dispatcharr_client,
            progress_callback=group_progress,
            generation=current_generation,  # Share generation across all groups
            service=shared_service,  # Reuse service to maintain warm cache
            # Step 4 below re-reads team AND group XMLTV and merges the lot;
            # aggregating the group half here would parse and serialize the
            # whole guide a second time for a value nothing reads.
            aggregate_xmltv=False,
            run_id=stats_run.id,  # Details + per-group breakdown key on this run (#645)
        )
        result.groups_processed = group_result.groups_processed
        result.groups_programmes = group_result.total_programmes
        result.programmes_total = result.teams_programmes + result.groups_programmes
        timer.mark("groups")

        # Step 3b: Global channel reassignment (if enabled)
        check_cancelled()
        _sync_global_channels(
            db_factory, dispatcharr_client, update_progress,
            external_occupied=external_occupied,
        )
        timer.mark("channel_reassign")

        # Step 3b: Apply stream ordering rules to all channels (93-95%)
        check_cancelled()
        update_progress("ordering", 93, "Applying stream ordering rules...")
        result.stream_ordering = _apply_stream_ordering(
            db_factory, dispatcharr_client, update_progress, manual=manual
        )
        timer.mark("stream_ordering")

        # Step 4: Merge and save XMLTV (95-96%)
        check_cancelled()
        update_progress("saving", 95, "Saving XMLTV...")

        xmltv_contents: list[str] = []
        with db_factory() as conn:
            team_xmltv = get_all_team_xmltv(conn)
            xmltv_contents.extend(team_xmltv)
            group_xmltv = get_all_group_xmltv(conn)
            xmltv_contents.extend(group_xmltv)

        output_path = settings.epg_output_path
        if xmltv_contents and output_path:
            merged_xmltv = merge_xmltv_content(
                xmltv_contents,
                generator_name=display_settings.xmltv_generator_name,
                generator_url=display_settings.xmltv_generator_url,
            )
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(merged_xmltv, encoding="utf-8")
            result.file_written = True
            result.file_path = str(output_file.absolute())
            result.file_size = len(merged_xmltv)
            logger.info(
                "[GENERATION] EPG written to %s (%s bytes)", output_path, f"{result.file_size:,}"
            )
        timer.mark("xmltv_save")

        # Create lifecycle service once for steps 5-6
        # Reuse shared_service to maintain cache warmth
        lifecycle_service = create_lifecycle_service(
            db_factory,
            shared_service,
            dispatcharr_client=dispatcharr_client,
        )
        # Compute external channel numbers to avoid collisions (#146)
        lifecycle_service.compute_external_occupied()
        lifecycle_service.sync_stream_profiles()

        # Step 5: Dispatcharr EPG refresh + channel association (96-98%)
        check_cancelled()
        if dispatcharr_client and dispatcharr_settings.epg_id:
            update_progress("dispatcharr", 96, "Refreshing Dispatcharr EPG...")

            raw_client = (
                dispatcharr_client.client
                if isinstance(dispatcharr_client, DispatcharrConnection)
                else dispatcharr_client
            )
            epg_manager = EPGManager(raw_client)
            refresh_result = epg_manager.wait_for_refresh(
                dispatcharr_settings.epg_id,
                timeout=300,
                cancellation_check=is_cancellation_requested,
            )
            result.epg_refresh = {
                "success": refresh_result.success,
                "message": refresh_result.message,
                "duration": refresh_result.duration,
            }

            update_progress("dispatcharr", 97, "Associating EPG with channels...")
            result.epg_association = lifecycle_service.associate_epg_with_channels(
                dispatcharr_settings.epg_id
            )
        timer.mark("dispatcharr_epg_refresh")

        # Steps 5b-5d: media-server guide refreshes — Emby, Jellyfin, and
        # Channels DVR servers ALL refresh in parallel (#471). They are
        # independent HTTP targets; per-server failures are isolated and
        # non-blocking, and one slow/offline server never delays the rest.
        check_cancelled()
        try:
            from teamarr.database.settings import (
                get_channelsdvr_settings,
                get_emby_settings,
                get_jellyfin_settings,
            )

            with db_factory() as conn:
                emby_settings = get_emby_settings(conn)
                jellyfin_settings = get_jellyfin_settings(conn)
                channelsdvr_settings = get_channelsdvr_settings(conn)

            jobs: list[tuple[str, Any]] = []
            if emby_settings.enabled:
                jobs += [("emby", s) for s in emby_settings.servers if s.url]
            if jellyfin_settings.enabled:
                jobs += [("jellyfin", s) for s in jellyfin_settings.servers if s.url]
            if channelsdvr_settings.enabled:
                jobs += [("channelsdvr", s) for s in channelsdvr_settings.servers if s.url]

            if jobs and _dry_run_media_refresh(result, jobs):
                jobs = []
            if jobs:
                update_progress(
                    "media_servers", 97,
                    f"Refreshing {len(jobs)} media server(s) in parallel...",
                )
                outcomes = _run_media_server_refreshes(
                    jobs, update_progress, is_cancellation_requested
                )
                result.media_server_outcomes = [
                    _media_server_outcome(kind, label, o) for kind, label, o in outcomes
                ]

                emby_results = [
                    {"server": label, **o["guide"]}
                    for kind, label, o in outcomes
                    if kind == "emby" and o.get("guide") is not None
                ]
                if emby_results:
                    result.emby_refresh = {
                        "success": all(r.get("success") for r in emby_results),
                        "servers": emby_results,
                    }

                jellyfin_results = [
                    {"server": label, **o["guide"]}
                    for kind, label, o in outcomes
                    if kind == "jellyfin" and o.get("guide") is not None
                ]
                if jellyfin_results:
                    result.jellyfin_refresh = {
                        "success": all(r.get("success") for r in jellyfin_results),
                        "servers": jellyfin_results,
                    }

                cdvr_m3u = [
                    {"server": label, **o["m3u"]}
                    for kind, label, o in outcomes
                    if kind == "channelsdvr" and o.get("m3u") is not None
                ]
                if cdvr_m3u:
                    result.channelsdvr_refresh = {
                        "success": all(r.get("success") for r in cdvr_m3u),
                        "servers": cdvr_m3u,
                    }
                cdvr_epg = [
                    {"server": label, **o["epg"]}
                    for kind, label, o in outcomes
                    if kind == "channelsdvr" and o.get("epg") is not None
                ]
                if cdvr_epg:
                    result.channelsdvr_epg_refresh = {
                        "success": all(r.get("success") for r in cdvr_epg),
                        "servers": cdvr_epg,
                    }
        except Exception as e:
            logger.warning("[MEDIA_SERVERS] Refresh failed (non-blocking): %s", e)
        timer.mark("media_server_refresh")

        # Step 6: Process scheduled deletions (98-99%)
        check_cancelled()
        update_progress("lifecycle", 98, "Processing scheduled deletions...")
        channels_deleted_count = 0
        try:
            deletion_result = lifecycle_service.process_scheduled_deletions()
            channels_deleted_count = len(deletion_result.deleted)
            result.deletions = {
                "deleted_count": channels_deleted_count,
                "error_count": len(deletion_result.errors),
            }
            if deletion_result.deleted:
                logger.info("[GENERATION] Deleted %d expired channel(s)", channels_deleted_count)
        except Exception as e:
            logger.warning("[GENERATION] Scheduled deletions failed: %s", e)
            result.deletions = {"error": str(e)}
        timer.mark("deletions")

        # Step 7: Run reconciliation + cleanup (99-100%)
        check_cancelled()
        update_progress("reconciliation", 99, "Running reconciliation...")
        try:
            with db_factory() as conn:
                recon_settings = get_reconciliation_settings(conn)
            if recon_settings.get("reconcile_on_epg_generation", True):
                reconciler = create_reconciler(db_factory, dispatcharr_client)
                recon_result = reconciler.reconcile(auto_fix=False)
                result.reconciliation = recon_result.summary
                if recon_result.issues_found:
                    logger.info("[RECONCILE] Found %d issue(s)", len(recon_result.issues_found))
        except Exception as e:
            logger.warning("[RECONCILE] Failed: %s", e)
            result.reconciliation = {"error": str(e)}
        timer.mark("reconciliation")

        # Step 7b: Stale source-group detection (lylt.1) — flag enabled groups
        # whose Dispatcharr M3U source channel-group no longer exists.
        try:
            detect_stale_groups(db_factory)
        except Exception as e:
            logger.warning("[STALE_GROUPS] Detection failed: %s", e)

        # DIAG: Post-generation stream audit — compare DB vs Dispatcharr
        try:
            _run_stream_audit(db_factory, dispatcharr_client)
        except Exception as e:
            logger.warning("[STREAM_AUDIT] Post-generation audit failed: %s", e)
        timer.mark("stream_audit")

        # Cleanup (history, old runs, unused logos — part of step 7)
        check_cancelled()
        update_progress("cleanup", 99, "Cleaning up history...")
        cleanup_results = _run_cleanup_tasks(db_factory, dispatcharr_client, update_progress)
        result.cleanup = cleanup_results["history"]
        result.logo_cleanup = cleanup_results["logos"]
        timer.mark("cleanup")
        logger.info("[GENERATION] Phase timings (s): %s", result.phase_timings)

        # Update and save stats run
        _finalize_stats_run(
            stats_run, result, team_result, group_result,
            channels_deleted_count, db_factory,
        )

        result.completed_at = time.time()
        result.duration_seconds = round(result.completed_at - result.started_at, 1)
        result.success = True

        update_progress("complete", 100, "Generation complete")

        # Flush the service cache to SQLite for immediate persistence

        flushed = flush_shared_cache()
        if flushed > 0:
            logger.debug("[CACHE] Flushed %d entries to SQLite", flushed)

    except GenerationCancelled:
        elapsed = round(time.time() - result.started_at, 1)
        logger.info("[GENERATION] Cancelled by user after %.1fs", elapsed)
        result.success = False
        result.error = "Cancelled by user"
        result.completed_at = time.time()
        result.duration_seconds = elapsed
        cancel_generation()

        # Save cancelled run
        try:
            from teamarr.database.stats import save_run as _save_run

            stats_run.complete(status="cancelled", error="Cancelled by user")
            with db_factory() as conn:
                _save_run(conn, stats_run)
        except Exception as save_err:
            logger.warning("[GENERATION] Failed to save cancelled run stats: %s", save_err)

    except Exception as e:
        logger.exception("[GENERATION] Failed: %s", e)
        result.success = False
        result.error = str(e)
        result.completed_at = time.time()
        result.duration_seconds = round(result.completed_at - result.started_at, 1)

        # Save failed run
        try:
            from teamarr.database.stats import save_run as _save_run

            stats_run.complete(status="failed", error=str(e))
            with db_factory() as conn:
                _save_run(conn, stats_run)
        except Exception as save_err:
            logger.warning("[GENERATION] Failed to save failed run stats: %s", save_err)

    finally:
        # Always release the lock
        _generation_running = False
        _generation_lock.release()

    return result


def _dry_run_media_refresh(result: Any, jobs: list[tuple[str, Any]]) -> bool:
    """DRY_RUN (#554): record what would have been refreshed, run nothing.

    Returns True when dry-run is active (caller skips the refresh jobs).
    """
    from teamarr.config.runtime import dry_run

    if not dry_run():
        return False
    by_kind: dict[str, list[str]] = {}
    for kind, server in jobs:
        by_kind.setdefault(kind, []).append(getattr(server, "url", None) or str(server))
    for kind, urls in by_kind.items():
        logger.info("[DRY_RUN] Suppressed %s guide refresh for %s", kind, ", ".join(urls))
        payload = {"success": True, "dry_run": True, "servers": urls}
        result.media_server_outcomes += [
            {"kind": kind, "server": u, "success": True, "duration": 0.0, "error": None,
             "dry_run": True}
            for u in urls
        ]
        if kind == "emby":
            result.emby_refresh = payload
        elif kind == "jellyfin":
            result.jellyfin_refresh = payload
        elif kind == "channelsdvr":
            result.channelsdvr_refresh = payload
            result.channelsdvr_epg_refresh = dict(payload)
    return True


def _run_media_server_refreshes(
    jobs: list[tuple[str, Any]],
    update_progress: Callable[..., None],
    is_cancellation_requested: Callable[[], bool],
) -> list[tuple[str, str, dict]]:
    """Run every media-server refresh job concurrently (#471).

    Each job is (kind, server) with kind in emby/jellyfin/channelsdvr.
    Returns (kind, label, outcome) triples where outcome carries "guide"
    (Emby/Jellyfin) or "m3u"/"epg" (Channels DVR) result dicts. A job that
    raises yields a failed "guide" outcome — never an exception.
    """
    from concurrent.futures import ThreadPoolExecutor

    results: list[tuple[str, str, dict]] = []
    with ThreadPoolExecutor(
        max_workers=min(8, len(jobs)), thread_name_prefix="media-refresh"
    ) as pool:
        futures = {
            pool.submit(
                _refresh_one_media_server,
                kind,
                server,
                update_progress,
                is_cancellation_requested,
            ): (kind, server)
            for kind, server in jobs
        }
        for future, (kind, server) in futures.items():
            label = server.name or server.url or ""
            try:
                results.append((kind, label, future.result()))
            except Exception as e:  # noqa: BLE001 — per-server isolation
                logger.warning(
                    "[%s] %s: refresh failed (non-blocking): %s",
                    kind.upper(),
                    label,
                    e,
                )
                results.append(
                    (kind, label, {"guide": {"success": False, "error": str(e)}})
                )
    return results


def _media_server_outcome(kind: str, label: str, outcome: dict) -> dict:
    """Flatten one server's refresh result for the run row (#649).

    Channels DVR has two steps (m3u + epg); it counts as a success only when
    both did, and reports the first error.
    """
    parts = [v for v in (outcome.get("guide"), outcome.get("m3u"), outcome.get("epg")) if v]
    errors = [p.get("error") or p.get("message") for p in parts if not p.get("success")]
    return {
        "kind": kind,
        "server": label,
        "success": bool(parts) and all(p.get("success") for p in parts),
        "duration": round(sum(float(p.get("duration") or 0) for p in parts), 2),
        "error": errors[0] if errors else None,
    }


def _refresh_one_media_server(
    kind: str,
    server: Any,
    update_progress: Callable[..., None],
    is_cancellation_requested: Callable[[], bool],
) -> dict:
    """Refresh a single media server (runs on a worker thread)."""
    label = server.name or server.url or ""

    if kind == "channelsdvr":
        m3u_res, epg_res = _refresh_channelsdvr_server(
            server,
            label,
            lambda msg: update_progress("channelsdvr", 97, f"{msg} ({label})"),
        )
        return {"m3u": m3u_res, "epg": epg_res}

    title = "Emby" if kind == "emby" else "Jellyfin"
    client_cls = EmbyClient if kind == "emby" else JellyfinClient
    client = client_cls(
        base_url=server.url,
        username=server.username or "",
        password=server.password or "",
        api_key=server.api_key,
    )

    update_progress(kind, 97, f"Refreshing {title} guide... ({label})")

    def on_progress(pct):
        update_progress(kind, 97, f"Refreshing {title} guide... ({label}) {pct:.0f}%")

    guide_result = client.trigger_guide_refresh(
        timeout=300,
        on_progress=on_progress,
        cancellation_check=is_cancellation_requested,
    )
    if guide_result.get("success"):
        logger.info(
            "[%s] %s: guide refresh completed in %.1fs",
            kind.upper(),
            label,
            guide_result.get("duration", 0),
        )
    else:
        logger.warning(
            "[%s] %s: guide refresh failed: %s",
            kind.upper(),
            label,
            guide_result.get("message"),
        )
    return {"guide": guide_result}


def _refresh_channelsdvr_server(
    server: Any,
    label: str,
    progress: Callable[[str], None],
) -> tuple[dict | None, dict | None]:
    """Refresh one Channels DVR server's M3U source, then its XMLTV lineup.

    Sequences the two refreshes on real evidence: waits for the M3U
    channel-list refresh to actually finish before firing the guide PUT,
    so the guide doesn't index against a stale channel list. Both waits
    poll CDVR /log (see client docs).

    Returns (m3u_result, epg_result); either is None when that phase
    didn't run (no source / no lineup configured).
    """
    # The client derives lineup_id as "XMLTV-<source_name>" when no lineup
    # is explicitly configured, so the guide refresh fires even if the
    # user only set the M3U source.
    client = ChannelsDVRClient(
        base_url=server.url,
        source_name=server.source_name or "",
        lineup_id=server.lineup_id or "",
    )

    if not (client.source_name or client.lineup_id):
        logger.warning(
            "[CHANNELSDVR] %s: enabled but no source name or XMLTV lineup "
            "configured — nothing to refresh. Set a source name "
            "(and optionally a lineup) in Settings.",
            label,
        )
        return None, None

    m3u_result: dict | None = None
    if client.source_name:
        progress("Refreshing Channels DVR channels...")
        m3u_result = client.trigger_m3u_refresh(
            timeout=60, wait_for_completion=bool(client.lineup_id)
        )
        if m3u_result.get("success"):
            logger.info(
                "[CHANNELSDVR] %s: M3U refresh triggered in %.1fs (completion: %s)",
                label,
                m3u_result.get("duration", 0),
                m3u_result.get("completed", "not awaited"),
            )
        else:
            logger.warning(
                "[CHANNELSDVR] %s: M3U refresh failed: %s",
                label,
                m3u_result.get("message"),
            )

    epg_result: dict | None = None
    if client.lineup_id:
        if client.lineup_derived:
            logger.info(
                "[CHANNELSDVR] %s: no XMLTV lineup configured; derived '%s' from source '%s'",
                label,
                client.lineup_id,
                client.source_name,
            )
        progress("Refreshing Channels DVR guide...")
        epg_result = client.trigger_epg_refresh(timeout=60, verify=True)
        if not epg_result.get("success"):
            logger.warning(
                "[CHANNELSDVR] %s: EPG refresh failed: %s",
                label,
                epg_result.get("message"),
            )
        else:
            verification = epg_result.get("verification") or {}
            status = verification.get("status")
            if status == "no_fetch":
                logger.warning(
                    "[CHANNELSDVR] %s: EPG refresh accepted but guide '%s' "
                    "was not re-fetched — guide may be stale",
                    label,
                    client.lineup_id,
                )
            else:
                logger.info(
                    "[CHANNELSDVR] %s: EPG refresh for lineup '%s' in %.1fs (verification: %s)",
                    label,
                    client.lineup_id,
                    epg_result.get("duration", 0),
                    status or "not verified",
                )
    else:
        logger.warning(
            "[CHANNELSDVR] %s: skipping EPG/guide refresh: no XMLTV lineup "
            "configured and none could be derived (set a source name so the "
            "lineup can be inferred). The guide will stay stale until "
            "refreshed manually.",
            label,
        )

    return m3u_result, epg_result


def _refresh_m3u_accounts(db_factory: Callable[[], Any], dispatcharr_client: Any) -> dict:
    """Refresh M3U accounts for all event groups."""
    from teamarr.database.groups import get_all_groups

    result = {"refreshed": 0, "skipped": 0, "failed": 0, "account_ids": []}

    # Collect unique M3U account IDs from active groups
    with db_factory() as conn:
        groups = get_all_groups(conn, include_disabled=False)

    account_ids = set()
    for group in groups:
        if group.m3u_account_id:
            account_ids.add(group.m3u_account_id)

    if not account_ids:
        return result

    result["account_ids"] = list(account_ids)

    # Refresh all accounts in parallel

    raw_client = (
        dispatcharr_client.client
        if isinstance(dispatcharr_client, DispatcharrConnection)
        else dispatcharr_client
    )
    m3u_manager = M3UManager(raw_client)
    batch_result = m3u_manager.refresh_multiple(
        list(account_ids),
        timeout=300,
        skip_if_recent_minutes=30,
    )

    result["refreshed"] = batch_result.succeeded_count - batch_result.skipped_count
    result["skipped"] = batch_result.skipped_count
    result["failed"] = batch_result.failed_count
    result["duration"] = batch_result.duration

    if batch_result.succeeded_count > 0:
        logger.info(
            "[M3U] Refresh: %d refreshed, %d skipped (recently updated)",
            result["refreshed"],
            result["skipped"],
        )

    return result


def _validate_channel_ranges(
    db_factory: Callable[[], Any],
    external_occupied: set[int],
) -> dict:
    """Validate global channel range against external Dispatcharr channels.

    Checks for overlap between the configured channel range and external
    channels. Returns conflict info for the generation result (#146).

    Args:
        db_factory: Factory function returning database connection
        external_occupied: Channel numbers occupied by non-Teamarr channels

    Returns:
        Dict with external channel stats and range warnings
    """
    from teamarr.database.channel_numbers import get_global_channel_range

    max_external = max(external_occupied) if external_occupied else 0
    conflicts: dict = {
        "external_channels_detected": len(external_occupied),
        "max_external_channel": max_external,
        "group_warnings": [],
    }

    with db_factory() as conn:
        range_start, range_end = get_global_channel_range(conn)
        effective_end = range_end if range_end else range_start + 9999
        global_range = set(range(range_start, effective_end + 1))
        collisions = external_occupied & global_range

        if collisions:
            available = len(global_range) - len(collisions)
            warning = {
                "group_id": None,
                "group_name": "Global Range",
                "range": f"{range_start}-{effective_end}",
                "external_collisions": len(collisions),
                "available_slots": available,
            }
            conflicts["group_warnings"].append(warning)
            logger.warning(
                "[CHANNEL_NUM] Global range %d-%d has %d "
                "external channel collisions (%d slots available)",
                range_start,
                effective_end,
                len(collisions),
                available,
            )

    if not conflicts["group_warnings"]:
        logger.info(
            "[CHANNEL_NUM] No channel range conflicts "
            "with %d external channels",
            len(external_occupied),
        )

    return conflicts


def _sync_global_channels(
    db_factory: Callable[[], Any],
    dispatcharr_client: Any | None,
    update_progress: Callable,
    external_occupied: set[int] | None = None,
) -> None:
    """Reassign channel numbers globally by sort priority.

    This is the single authoritative pass that pushes numbers to Dispatcharr.
    In sticky (gap/strict) modes it places only new channels, unless the daily
    reset window has arrived (should_run_channel_reset) — then it re-grids
    everything once.
    """
    from teamarr.database.channel_numbers import (
        reassign_all_channels,
        should_run_channel_reset,
    )

    update_progress("groups", 94, "Reassigning channels globally by sport/league priority...")
    with db_factory() as conn:
        force_reset = should_run_channel_reset(conn)
        if force_reset:
            update_progress("groups", 94, "Daily channel re-layout (low-traffic reset)...")
        global_result = reassign_all_channels(
            conn, external_occupied=external_occupied, force_reset=force_reset
        )
        if global_result["channels_moved"] == 0:
            return

        logger.info(
            "[GENERATION] Global reassignment: %d channels processed, %d moved",
            global_result["channels_processed"],
            global_result["channels_moved"],
        )

        if not dispatcharr_client:
            return

        synced = 0
        for ch in global_result.get("drift_details", []):
            disp_id = ch.get("dispatcharr_channel_id")
            new_num = ch.get("new_number")
            if disp_id and new_num:
                try:
                    dispatcharr_client.channels.update_channel(
                        disp_id, {"channel_number": new_num}
                    )
                    synced += 1
                except Exception as e:
                    logger.warning(
                        "[GENERATION] Failed to sync channel %s to Dispatcharr: %s",
                        ch.get("channel_name"),
                        e,
                    )
        if synced:
            logger.info("[GENERATION] Synced %d channel numbers to Dispatcharr", synced)


@dataclass
class _OrderingPlan:
    """One channel's decided stream-ordering state, ready for the push phase.

    Carries everything the push decision needs so phase 2 can run without going
    back to the database and phase 3 can run without touching it at all (#735).
    """

    channel: Any
    current_order: list[int] | None  # what Dispatcharr is holding (#712)
    pinned_top: int | None  # live-event #1 pin (#232)
    reordered_count: int
    has_windowed: bool


def _push_stream_orders(
    channel_mgr: Any,
    pushes: list[tuple["_OrderingPlan", list[int]]],
    update_progress: Callable,
) -> int:
    """PATCH each channel's stream order to Dispatcharr, concurrently (#735).

    One PATCH per channel used to go out serially, and since #712 any order
    difference triggers one — so a windowed install re-pushes most of its
    channels every run and this was the longest serial stretch left. The work
    is pure network wait: ``update_channel`` guards only a cache write, and the
    client pools its connections.

    Failures are logged and counted, never raised: Dispatcharr still holds the
    wrong order, so the drift check re-detects it and retries next run. Nothing
    is rolled back for the same reason (see #712).

    Returns:
        The number of channels whose push failed.
    """
    workers = min(_ORDERING_PUSH_WORKERS, len(pushes))
    failures = 0
    done = 0
    total = len(pushes)

    def push(plan: "_OrderingPlan", ordered_ids: list[int]):
        return plan, channel_mgr.update_channel(
            plan.channel.dispatcharr_channel_id, {"streams": ordered_ids}
        )

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="order-push") as executor:
        futures = [executor.submit(push, plan, ids) for plan, ids in pushes]
        for future in as_completed(futures):
            done += 1
            try:
                plan, sync_result = future.result()
            except Exception as e:  # noqa: BLE001 — per-channel isolation
                failures += 1
                logger.warning("[ORDERING] Stream-order push raised: %s", e)
            else:
                if not sync_result.success:
                    failures += 1
                    logger.warning(
                        "[ORDERING] Failed to sync channel %s to Dispatcharr: %s",
                        plan.channel.channel_name,
                        sync_result.error,
                    )
            if done % 10 == 0 or done == total:
                pct = 93 + int((done / total) * 2)
                update_progress(
                    "ordering", pct, f"Ordering streams ({done}/{total})", done, total, ""
                )

    logger.info(
        "[ORDERING] Pushed stream order for %d channel(s) across %d worker(s); %d failed",
        total, workers, failures,
    )
    return failures


def _apply_stream_ordering(
    db_factory: Callable[[], Any],
    dispatcharr_client: Any | None,
    update_progress: Callable,
    manual: bool = False,
) -> dict:
    """Apply stream ordering rules to all managed channels.

    ``manual`` (#232): a user-triggered run bypasses the live-event #1 pin —
    the escape hatch when the pinned stream is wrong. Scheduled runs keep the
    top slot of an in-window event's channel stable so a re-gen can't displace
    the stream a viewer is currently watching; rule-truth priorities are still
    persisted, so normal ordering resumes on the first post-event push. That
    post-event push is guaranteed since #712: once the pin lifts, the intended
    order no longer matches what Dispatcharr holds, and order drift alone is
    enough to trigger a push.
    """
    from teamarr.consumers.lifecycle import is_channel_event_live
    from teamarr.database.channels import (
        get_all_channel_streams,
        get_all_managed_channels,
        get_all_ordered_stream_ids,
        update_stream_priority,
    )
    from teamarr.database.channels.streams import (
        get_active_dispatcharr_stream_ids,
        refresh_stream_stats_bulk,
    )
    from teamarr.database.settings import get_stream_ordering_settings
    from teamarr.utilities.tz import now_utc

    reorder_result: dict = {
        "channels_reordered": 0,
        "streams_reordered": 0,
        "windows_synced": 0,
        "order_drift_synced": 0,
        "stats_refreshed": 0,
    }
    try:
        with db_factory() as conn:
            ordering_settings = get_stream_ordering_settings(conn)
            # No early return when rules are absent: time-windowed (EPG-matched)
            # streams still need their membership synced each run so they attach
            # when their window opens and detach when it closes (bead teamarrv2-uye).
            ordering_service = (
                StreamOrderingService(rules=ordering_settings.rules, conn=conn)
                if ordering_settings.rules
                else None
            )
            if ordering_service:
                logger.info(
                    "[ORDERING] Applying %d ordering rule(s)", len(ordering_settings.rules)
                )
            else:
                logger.debug(
                    "[ORDERING] No ordering rules configured; running window sync only"
                )

            # Setup Dispatcharr channel manager once if available
            channel_mgr = None
            if dispatcharr_client:

                raw_client = (
                    dispatcharr_client.client
                    if isinstance(dispatcharr_client, DispatcharrConnection)
                    else dispatcharr_client
                )
                channel_mgr = ChannelManager(raw_client)

            # Dispatcharr's ACTUAL stream order per channel (#712). Until now the
            # push below was gated purely on whether a local priority CHANGED, and
            # priorities are already computed at insert time
            # (creator.py::compute_stream_priority_from_rules) — so a steady-state
            # channel recomputes to the value it already has, reordered_count is 0,
            # and Teamarr never pushed no matter what order Dispatcharr was holding.
            # Any divergence (a rejected push, a hand edit in Dispatcharr, a
            # reconciliation fix that wrote the wrong order) was therefore permanent.
            # One list call on a fresh manager cache — the audit at the end of the
            # run pays the same cost — gives us the real order to converge against.
            dispatcharr_order: dict[int, list[int]] = {}
            if channel_mgr:
                try:
                    for d_channel in channel_mgr.get_channels():
                        dispatcharr_order[d_channel.id] = list(d_channel.streams or ())
                except Exception as e:
                    # Order convergence is best-effort: without it we fall back to
                    # the old change-gated behavior rather than failing the run.
                    logger.warning("[ORDERING] Could not read Dispatcharr stream order: %s", e)

            all_channels = get_all_managed_channels(conn, include_deleted=False)

            # One scan each instead of two queries per channel (#735). The
            # window instant is pinned for the whole pass so every channel's
            # attach/detach state is evaluated at the same moment — walking
            # channels one at a time re-read the clock per channel, so a long
            # pass could open a window partway through and treat two channels
            # sharing a stream inconsistently.
            window_now = now_utc().strftime("%Y-%m-%d %H:%M:%S")
            streams_by_channel = get_all_channel_streams(conn)
            pre_order_by_channel = get_all_ordered_stream_ids(conn, now=window_now)

            # stats_metric rules score against the cached stream_stats column,
            # and until now that column was only ever refreshed by the streams
            # page — one endpoint, driven by a human opening a channel (#576,
            # #616). Every scheduled run therefore ordered on whatever numbers
            # happened to be cached, which on a real install means months old or
            # absent entirely. Pull them once, in bulk, before any priority is
            # computed.
            #
            # Skipped outright when no rule reads stats: the fetch buys nothing
            # for a ruleset built from m3u/group/regex, and most are.
            if ordering_service and any(
                rule.type == "stats_metric" for rule in ordering_settings.rules
            ):
                stat_stream_ids = get_active_dispatcharr_stream_ids(conn)
                refreshed = refresh_stream_stats_bulk(conn, stat_stream_ids)
                reorder_result["stats_refreshed"] = refreshed
                logger.info(
                    "[ORDERING] Refreshed stats for %d/%d stream(s) before scoring",
                    refreshed,
                    len(stat_stream_ids),
                )

            # Phase 1 (serial, database): recompute and persist priorities.
            # Nothing here touches the network, so it stays on the one
            # connection; the pushes are batched up and issued afterwards.
            plans: list[_OrderingPlan] = []

            for channel in all_channels:
                streams = streams_by_channel.get(channel.id)
                if not streams:
                    continue

                current_order = (
                    dispatcharr_order.get(channel.dispatcharr_channel_id)
                    if channel.dispatcharr_channel_id
                    else None
                )

                # Live-event #1 pin (#232): capture the currently-pushed top
                # stream BEFORE priorities are recomputed, so the push below
                # can keep it in the top slot mid-broadcast.
                #
                # Read it from Dispatcharr's real order when we have it (#712):
                # the pin's premise is that slot 1 is what somebody is watching,
                # and that is Dispatcharr's slot 1, not ours. Falling back to the
                # DB order would pin the wrong stream on exactly the channels
                # whose order has drifted — and now that drift alone triggers a
                # push, that would displace the live stream instead of leaving it
                # alone.
                pinned_top: int | None = None
                if not manual and is_channel_event_live(
                    channel.event_date, channel.scheduled_delete_at
                ):
                    pre_order = (
                        current_order
                        if current_order is not None
                        else pre_order_by_channel.get(channel.id, [])
                    )
                    pinned_top = pre_order[0] if pre_order else None
                    # The pin's premise is that slot 1 is what somebody is
                    # watching. A probe saying the stream is dead or a black
                    # screen contradicts that premise, and the pin has to yield
                    # to it (#670): an in-flight session has already failed over
                    # to slot 2, while every new tune-in pays the failover
                    # again. Holding it would also silently undo the demotion a
                    # stats_metric rule just made — the priority lands in the DB
                    # and never reaches Dispatcharr — for the whole live window.
                    #
                    # Only a measurement lifts the pin. No stats, absent keys
                    # and unreadable stats all leave it in place, because those
                    # say nothing about the stream and holding when nothing is
                    # known is exactly what the pin is for.
                    if pinned_top is not None:
                        top = next(
                            (s for s in streams if s.dispatcharr_stream_id == pinned_top), None
                        )
                        if top is not None and top.measured_dead_or_blank:
                            logger.info(
                                "[STREAM_AUDIT] pin: ch='%s' (d_id=%s) live event — "
                                "released stream %d from #1, measured dead/blank (#670)",
                                channel.channel_name,
                                channel.dispatcharr_channel_id,
                                pinned_top,
                            )
                            pinned_top = None

                reordered_count = 0
                if ordering_service:
                    for stream in streams:
                        new_priority = ordering_service.compute_priority(stream)
                        if stream.priority != new_priority:
                            update_stream_priority(conn, stream.id, new_priority)
                            reordered_count += 1

                if reordered_count > 0:
                    reorder_result["channels_reordered"] += 1
                    reorder_result["streams_reordered"] += reordered_count

                if channel_mgr and channel.dispatcharr_channel_id:
                    plans.append(
                        _OrderingPlan(
                            channel=channel,
                            current_order=current_order,
                            pinned_top=pinned_top,
                            reordered_count=reordered_count,
                            # A channel with any time-windowed stream must be
                            # synced every run: membership flips as the window
                            # opens and closes.
                            has_windowed=any(s.attach_at for s in streams),
                        )
                    )

            # Phase 2 (serial, database): one scan for the post-update active
            # sets, then decide what actually needs pushing.
            post_order_by_channel = get_all_ordered_stream_ids(conn, now=window_now)
            pushes: list[tuple[_OrderingPlan, list[int]]] = []

            for plan in plans:
                channel = plan.channel
                # An empty set IS pushed — a channel whose sole source is
                # currently out-of-window must be cleared (it re-attaches on a
                # later run).
                ordered_ids = list(post_order_by_channel.get(channel.id, []))

                if (
                    plan.pinned_top is not None
                    and ordered_ids
                    and ordered_ids[0] != plan.pinned_top
                    and plan.pinned_top in ordered_ids
                ):
                    # Event is live: rule-truth priorities are in the DB, but
                    # the push keeps the current #1 on top so the stream being
                    # watched isn't displaced mid-broadcast.
                    ordered_ids.remove(plan.pinned_top)
                    ordered_ids.insert(0, plan.pinned_top)
                    logger.info(
                        "[STREAM_AUDIT] pin: ch='%s' (d_id=%s) live event — "
                        "kept stream %d at #1 (#232)",
                        channel.channel_name,
                        channel.dispatcharr_channel_id,
                        plan.pinned_top,
                    )

                # Compared AFTER the pin is applied, so a pinned channel is
                # measured against the order we actually intend to push and
                # doesn't re-push every run.
                order_drifted = (
                    plan.current_order is not None and ordered_ids != plan.current_order
                )

                if not (plan.reordered_count > 0 or plan.has_windowed or order_drifted):
                    continue

                if plan.has_windowed:
                    reorder_result["windows_synced"] += 1
                if order_drifted and plan.reordered_count == 0 and not plan.has_windowed:
                    reorder_result["order_drift_synced"] += 1
                    logger.info(
                        "[STREAM_AUDIT] drift: ch='%s' (d_id=%s) Dispatcharr order "
                        "%s does not match intended %s — re-pushing (#712)",
                        channel.channel_name,
                        channel.dispatcharr_channel_id,
                        plan.current_order,
                        ordered_ids,
                    )
                logger.info(
                    "[STREAM_AUDIT] sync: ch='%s' (d_id=%s) setting streams=%s "
                    "count=%d (reordered=%d windowed=%s drifted=%s)",
                    channel.channel_name,
                    channel.dispatcharr_channel_id,
                    ordered_ids,
                    len(ordered_ids),
                    plan.reordered_count,
                    plan.has_windowed,
                    order_drifted,
                )
                pushes.append((plan, ordered_ids))

        # Phase 3 (parallel, network only): issue the pushes. Outside the `with`
        # so the database connection is closed before any thread runs — every
        # decision is already made and nothing below writes to it.
        #
        # This was the longest serial stretch left in a run: one PATCH per
        # channel, and since #712 any order difference triggers one, so a
        # windowed install pushes most of its channels every run. update_channel
        # is thread-safe (its only shared state is a locked cache write) and the
        # client pools connections, so the wait is pure overlap.
        if pushes:
            failed = _push_stream_orders(channel_mgr, pushes, update_progress)
            reorder_result["push_failures"] = failed

        if (
            reorder_result["channels_reordered"] > 0
            or reorder_result["windows_synced"] > 0
            or reorder_result["order_drift_synced"] > 0
        ):
            logger.info(
                "[ORDERING] Reordered %d streams across %d channels; "
                "window-synced %d channel(s); order-drift re-pushed %d channel(s)",
                reorder_result["streams_reordered"],
                reorder_result["channels_reordered"],
                reorder_result["windows_synced"],
                reorder_result["order_drift_synced"],
            )
    except Exception as e:
        logger.warning("[ORDERING] Stream ordering failed: %s", e)
        reorder_result["error"] = str(e)

    return reorder_result


def _run_stream_audit(
    db_factory: Callable[[], Any],
    dispatcharr_client: Any | None,
) -> None:
    """Post-generation audit: compare DB stream counts vs Dispatcharr.

    Logs any channels where the DB and Dispatcharr disagree on stream
    assignments. This is diagnostic-only — no changes are made.
    """
    from teamarr.consumers.lifecycle import is_channel_event_live
    from teamarr.database.channels import get_all_managed_channels, get_all_ordered_stream_ids

    if not dispatcharr_client:
        return

    channel_attr = getattr(dispatcharr_client, "channels", None)
    raw_client = channel_attr._client if channel_attr else None
    if not raw_client:
        return

    channel_mgr = ChannelManager(raw_client)
    mismatches = []
    order_mismatches = []

    with db_factory() as conn:
        channels = get_all_managed_channels(conn, include_deleted=False)
        # One scan for every channel's active set instead of a query each (#735).
        ordered_by_channel = get_all_ordered_stream_ids(conn)

        for channel in channels:
            if not channel.dispatcharr_channel_id:
                continue

            # Window-gated active set (same set we actually push to Dispatcharr).
            # Using the raw stream list here would false-flag time-shared EPG
            # streams that are correctly out of their attach/detach window (183.5)
            # as mismatches. Mirrors reconciliation's expected-set logic.
            #
            # Kept in priority order (#712): this audit used to sort both sides
            # before comparing, so it could only ever see membership — it logged
            # "All channels match" on runs where Dispatcharr held a visibly
            # different order. Order is the whole point of the ordering step, so
            # the audit has to be able to see it.
            db_stream_ids = ordered_by_channel.get(channel.id, [])

            d_channel = channel_mgr.get_channel(channel.dispatcharr_channel_id)
            if not d_channel:
                logger.warning(
                    "[STREAM_AUDIT] MISSING: ch='%s' (d_id=%s) exists in DB "
                    "but not in Dispatcharr (db_streams=%s)",
                    channel.channel_name,
                    channel.dispatcharr_channel_id,
                    db_stream_ids,
                )
                continue

            d_stream_ids = list(d_channel.streams or ())

            if sorted(db_stream_ids) != sorted(d_stream_ids):
                mismatches.append(channel.channel_name)
                logger.warning(
                    "[STREAM_AUDIT] MISMATCH: ch='%s' (d_id=%s) "
                    "db_streams=%s (%d) vs dispatcharr_streams=%s (%d)",
                    channel.channel_name,
                    channel.dispatcharr_channel_id,
                    sorted(db_stream_ids),
                    len(db_stream_ids),
                    sorted(d_stream_ids),
                    len(d_stream_ids),
                )
            elif db_stream_ids != d_stream_ids:
                # Same streams, different order. Expected on a live channel: the
                # #1 pin (#232) deliberately holds the watched stream on top,
                # against the DB's rule-truth order, until the event ends.
                if is_channel_event_live(channel.event_date, channel.scheduled_delete_at):
                    logger.debug(
                        "[STREAM_AUDIT] pinned order: ch='%s' (d_id=%s) "
                        "db_order=%s vs dispatcharr_order=%s (live event, #232)",
                        channel.channel_name,
                        channel.dispatcharr_channel_id,
                        db_stream_ids,
                        d_stream_ids,
                    )
                else:
                    order_mismatches.append(channel.channel_name)
                    logger.warning(
                        "[STREAM_AUDIT] ORDER MISMATCH: ch='%s' (d_id=%s) same %d "
                        "stream(s), different order: db_order=%s vs dispatcharr_order=%s",
                        channel.channel_name,
                        channel.dispatcharr_channel_id,
                        len(db_stream_ids),
                        db_stream_ids,
                        d_stream_ids,
                    )

    if mismatches:
        logger.warning(
            "[STREAM_AUDIT] %d channel(s) have stream mismatches: %s",
            len(mismatches),
            mismatches[:20],  # Cap at 20 to avoid log spam
        )
    if order_mismatches:
        logger.warning(
            "[STREAM_AUDIT] %d channel(s) have stream ORDER mismatches: %s",
            len(order_mismatches),
            order_mismatches[:20],
        )
    if not mismatches and not order_mismatches:
        logger.info("[STREAM_AUDIT] All channels match between DB and Dispatcharr")


def _run_cleanup_tasks(
    db_factory: Callable[[], Any],
    dispatcharr_client: Any | None,
    update_progress: Callable,
) -> dict:
    """Run all post-generation cleanup: history, old runs, unused logos."""
    from teamarr.database.channels import cleanup_old_history, get_reconciliation_settings

    results: dict = {"history": {}, "logos": {}}

    # History cleanup
    try:
        with db_factory() as conn:
            cleanup_settings = get_reconciliation_settings(conn)
            retention_days = cleanup_settings.get("channel_history_retention_days", 90)
            deleted_count = cleanup_old_history(conn, retention_days)
            results["history"] = {"deleted_count": deleted_count}
            if deleted_count > 0:
                logger.info("[CLEANUP] Removed %d old history record(s)", deleted_count)
    except Exception as e:
        logger.warning("[CLEANUP] History cleanup failed: %s", e)
        results["history"] = {"error": str(e)}

    # Old processing runs (>30 days)
    try:
        from teamarr.database.stats import cleanup_old_runs

        with db_factory() as conn:
            runs_deleted = cleanup_old_runs(conn, days=30)
            if runs_deleted > 0:
                logger.info("[CLEANUP] Removed %d old processing run(s)", runs_deleted)
    except Exception as e:
        logger.warning("[CLEANUP] Run history cleanup failed: %s", e)

    # Unused logos
    try:
        from teamarr.database.settings import get_dispatcharr_settings

        with db_factory() as conn:
            dispatcharr_settings = get_dispatcharr_settings(conn)
        if dispatcharr_settings.cleanup_unused_logos and dispatcharr_client:
            update_progress("cleanup", 99, "Cleaning up unused logos...")
            cleanup_result = dispatcharr_client.logos.cleanup_unused()
            if cleanup_result.success:
                logos_deleted = (
                    cleanup_result.data.get("deleted_count", 0) if cleanup_result.data else 0
                )
                results["logos"] = {"deleted_count": logos_deleted}
                if logos_deleted > 0:
                    logger.info("[CLEANUP] Removed %d unused logo(s)", logos_deleted)
            else:
                logger.warning("[CLEANUP] Logo cleanup failed: %s", cleanup_result.error)
                results["logos"] = {"error": cleanup_result.error}
    except Exception as e:
        logger.warning("[CLEANUP] Logo cleanup failed: %s", e)
        results["logos"] = {"error": str(e)}

    return results


def _finalize_stats_run(
    stats_run: Any,
    result: GenerationResult,
    team_result: Any,
    group_result: Any,
    channels_deleted_count: int,
    db_factory: Callable[[], Any],
) -> None:
    """Populate stats run with generation results and save to database."""
    from teamarr.database.channels import get_all_managed_channels
    from teamarr.database.stats import save_run

    stats_run.programmes_total = result.programmes_total
    stats_run.programmes_events = team_result.total_events + group_result.total_events
    stats_run.programmes_pregame = team_result.total_pregame + group_result.total_pregame
    stats_run.programmes_postgame = team_result.total_postgame + group_result.total_postgame
    stats_run.programmes_idle = team_result.total_idle
    stats_run.channels_created = group_result.total_channels_created
    stats_run.channels_updated = group_result.total_channels_updated
    stats_run.channels_skipped = group_result.total_channels_skipped
    stats_run.channels_errors = group_result.total_channel_errors
    stats_run.channels_deleted = channels_deleted_count + group_result.total_channels_deleted
    stats_run.xmltv_size_bytes = result.file_size
    stats_run.streams_fetched = group_result.total_streams_fetched
    stats_run.streams_matched = group_result.total_streams_matched
    stats_run.streams_unmatched = group_result.total_streams_unmatched
    stats_run.streams_cached = group_result.total_streams_cached
    stats_run.extra_metrics["teams_processed"] = result.teams_processed
    stats_run.extra_metrics["groups_processed"] = result.groups_processed
    # Per-group breakdown (#645): replaces the old one-row-per-group sub-runs.
    stats_run.extra_metrics["groups"] = group_result.group_summaries()
    stats_run.extra_metrics["file_written"] = result.file_written

    # Post-processing enforcement outcomes (iua3.7): one record per step with
    # ok/count/error, so a silently failing enforcement step shows up in the
    # run summary instead of only in warning logs.
    if getattr(group_result, "enforcement", None):
        stats_run.extra_metrics["enforcement"] = [
            step.to_dict() for step in group_result.enforcement
        ]

    # Provider HTTP call volume for this run (kbbk). The per-endpoint breakdown
    # and total let the run summary surface calls-per-channel, making a
    # call-volume regression (the #254 refetch bug class) visible. Snapshot the
    # run-scoped counter that was reset at run start.

    stats_run.extra_metrics["provider_calls"] = call_metrics.snapshot()
    stats_run.extra_metrics["provider_calls_total"] = call_metrics.total()

    # Media-server refresh outcomes (#649): non-blocking failures otherwise
    # leave no trace beyond a phase timing collapsing to ~0.
    if result.media_server_outcomes:
        stats_run.extra_metrics["media_servers"] = list(result.media_server_outcomes)

    # Per-phase wall time so run-to-run comparisons (and perf regressions)
    # are visible in the run summary instead of requiring log archaeology.
    if result.phase_timings:
        stats_run.extra_metrics["phase_timings"] = dict(result.phase_timings)

    with db_factory() as conn:
        active_channels = get_all_managed_channels(conn, include_deleted=False)
        stats_run.channels_active = len(active_channels)
        logger.info("[GENERATION] %d active managed channels", len(active_channels))

    stats_run.complete(status="completed")

    with db_factory() as conn:
        save_run(conn, stats_run)
