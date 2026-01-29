"""Unified EPG generation workflow.

This module provides the single source of truth for EPG generation.
Both the streaming API endpoint and the background scheduler call this.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Global lock to prevent concurrent EPG generation runs
_generation_lock = threading.Lock()
_generation_running = False


class AbortedError(Exception):
    """Raised when generation is aborted by user."""

    pass


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

    # For stats run tracking
    run_id: int | None = None


# Type alias for progress callback
# (phase: str, percent: int, message: str, current: int, total: int, item_name: str) -> None
ProgressCallback = Callable[[str, int, str, int, int, str], None]

# () -> bool - returns True if abort requested
AbortCheck = Callable[[], bool]


def run_full_generation(
    db_factory: Callable[[], Any],
    dispatcharr_client: Any | None = None,
    progress_callback: ProgressCallback | None = None,
    abort_check: AbortCheck | None = None,
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
        process_all_event_groups,
        process_all_teams,
    )
    from teamarr.consumers.team_processor import get_all_team_xmltv
    from teamarr.database.channels import cleanup_old_history, get_reconciliation_settings
    from teamarr.database.groups import get_all_group_xmltv
    from teamarr.database.settings import (
        get_dispatcharr_settings,
        get_display_settings,
        get_epg_settings,
    )
    from teamarr.database.stats import create_run, save_run
    from teamarr.dispatcharr import EPGManager
    from teamarr.services import create_default_service
    from teamarr.utilities.xmltv import merge_xmltv_content

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

    def check_abort() -> bool:
        """Check if abort was requested and handle it."""
        if abort_check and abort_check():
            logger.info("[GENERATION] Abort requested, stopping...")
            result.success = False
            result.error = "Aborted by user"
            return True
        return False

    try:
        # Increment generation counter ONCE at start of full EPG run
        # This ensures all groups in this run share the same generation
        from teamarr.consumers.stream_match_cache import increment_generation_counter

        current_generation = increment_generation_counter(db_factory)
        logger.info("[GENERATION] Starting with cache generation %d", current_generation)

        # Create a single SportsDataService instance to share across all processing
        # This ensures the event cache stays warm throughout the entire run
        # (Previously each consumer created its own service with a cold cache)
        shared_service = create_default_service()

        # Get settings
        with db_factory() as conn:
            settings = get_epg_settings(conn)
            dispatcharr_settings = get_dispatcharr_settings(conn)
            display_settings = get_display_settings(conn)

        # Step 1: Refresh M3U accounts (0-5%)
        update_progress("init", 3, "Refreshing M3U accounts...")
        if dispatcharr_client:
            result.m3u_refresh = _refresh_m3u_accounts(db_factory, dispatcharr_client)

        # Check for abort before teams
        if check_abort():
            raise AbortedError("Generation aborted before team processing")

        # Step 2: Process all teams (5-50%) - 45% budget
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

        # Check for abort before event groups
        if check_abort():
            raise AbortedError("Generation aborted before event group processing")

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

        group_result = process_all_event_groups(
            db_factory=db_factory,
            dispatcharr_client=dispatcharr_client,
            progress_callback=group_progress,
            generation=current_generation,  # Share generation across all groups
            service=shared_service,  # Reuse service to maintain warm cache
        )
        result.groups_processed = group_result.groups_processed
        result.groups_programmes = group_result.total_programmes
        result.programmes_total = result.teams_programmes + result.groups_programmes

        # Check for abort before saving/dispatcharr sync
        if check_abort():
            raise AbortedError("Generation aborted before saving XMLTV")

        # Step 3b: Global channel reassignment (if enabled)
        # Applies when sorting_scope is "global" - interleaves channels by sport/league/time
        from teamarr.database.channel_numbers import reassign_channels_globally
        from teamarr.database.settings import get_channel_numbering_settings

        with db_factory() as conn:
            channel_numbering = get_channel_numbering_settings(conn)

        if channel_numbering.sorting_scope == "global":
            update_progress(
                "groups", 94, "Reassigning channels globally by sport/league priority..."
            )
            with db_factory() as conn:
                global_result = reassign_channels_globally(conn)
                if global_result["channels_moved"] > 0:
                    logger.info(
                        "[GENERATION] Global reassignment: %d channels processed, %d moved",
                        global_result["channels_processed"],
                        global_result["channels_moved"],
                    )

                    # Sync moved channel numbers to Dispatcharr
                    if dispatcharr_client:
                        synced = 0
                        for ch in global_result.get("drift_details", []):
                            disp_id = ch.get("dispatcharr_channel_id")
                            new_num = ch.get("new_number")
                            if disp_id and new_num:
                                try:
                                    dispatcharr_client.channels.update_channel(
                                        disp_id,
                                        {"channel_number": new_num},
                                    )
                                    synced += 1
                                except Exception as e:
                                    logger.warning(
                                        "[GENERATION] Failed to sync channel %s to Dispatcharr: %s",
                                        ch.get("channel_name"),
                                        e,
                                    )
                        if synced:
                            logger.info(
                                "[GENERATION] Synced %d channel numbers to Dispatcharr", synced
                            )

        # Step 3b: Apply stream ordering rules to all channels (93-95%)
        update_progress("ordering", 93, "Applying stream ordering rules...")
        from teamarr.database.channels import (
            get_all_managed_channels,
            get_ordered_stream_ids,
        )

        reorder_result = {"channels_reordered": 0, "streams_reordered": 0}
        try:
            from teamarr.database.channels import get_channel_streams, update_stream_priority
            from teamarr.database.settings import get_stream_ordering_settings
            from teamarr.services.stream_ordering import StreamOrderingService

            with db_factory() as conn:
                # Load ordering rules once
                ordering_settings = get_stream_ordering_settings(conn)
                if not ordering_settings.rules:
                    logger.debug("[ORDERING] No stream ordering rules configured, skipping")
                else:
                    # Create ordering service once with rules
                    ordering_service = StreamOrderingService(
                        rules=ordering_settings.rules, conn=conn
                    )
                    logger.info(
                        "[ORDERING] Applying %d ordering rule(s)", len(ordering_settings.rules)
                    )

                    # Setup Dispatcharr channel manager once if available
                    channel_mgr = None
                    if dispatcharr_client:
                        from teamarr.dispatcharr.factory import DispatcharrConnection
                        from teamarr.dispatcharr.managers import ChannelManager

                        raw_client = (
                            dispatcharr_client.client
                            if isinstance(dispatcharr_client, DispatcharrConnection)
                            else dispatcharr_client
                        )
                        channel_mgr = ChannelManager(raw_client)

                    # Get all active channels
                    all_channels = get_all_managed_channels(conn, include_deleted=False)
                    total_channels = len(all_channels)

                    for idx, channel in enumerate(all_channels):
                        # Get streams for this channel
                        streams = get_channel_streams(conn, channel.id)
                        if not streams:
                            continue

                        # Compute new priorities using the shared ordering service
                        reordered_count = 0
                        for stream in streams:
                            new_priority = ordering_service.compute_priority(stream)
                            if stream.priority != new_priority:
                                update_stream_priority(conn, stream.id, new_priority)
                                reordered_count += 1

                        if reordered_count > 0:
                            reorder_result["channels_reordered"] += 1
                            reorder_result["streams_reordered"] += reordered_count

                            # Sync ordered streams to Dispatcharr
                            if channel_mgr and channel.dispatcharr_channel_id:
                                ordered_ids = get_ordered_stream_ids(conn, channel.id)
                                if ordered_ids:
                                    sync_result = channel_mgr.update_channel(
                                        channel.dispatcharr_channel_id, {"streams": ordered_ids}
                                    )
                                    if not sync_result.success:
                                        logger.warning(
                                            "[ORDERING] Failed to sync channel %s to Dispatcharr: %s",  # noqa: E501
                                            channel.channel_name,
                                            sync_result.error,
                                        )

                        # Update progress every 10 channels or at end
                        if (idx + 1) % 10 == 0 or idx == total_channels - 1:
                            pct = 93 + int(((idx + 1) / total_channels) * 2)
                            update_progress(
                                "ordering",
                                pct,
                                f"Ordering streams ({idx + 1}/{total_channels})",
                                idx + 1,
                                total_channels,
                                channel.channel_name,
                            )

                    if reorder_result["channels_reordered"] > 0:
                        logger.info(
                            "[ORDERING] Reordered %d streams across %d channels",
                            reorder_result["streams_reordered"],
                            reorder_result["channels_reordered"],
                        )
        except Exception as e:
            logger.warning("[ORDERING] Stream ordering failed: %s", e)
            reorder_result["error"] = str(e)

        result.stream_ordering = reorder_result

        # Step 4: Merge and save XMLTV (95-96%)
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

        # Create lifecycle service once for steps 5-6
        # Reuse shared_service to maintain cache warmth
        lifecycle_service = create_lifecycle_service(
            db_factory,
            shared_service,
            dispatcharr_client=dispatcharr_client,
        )

        # Step 5: Dispatcharr EPG refresh + channel association (96-98%)
        if dispatcharr_client and dispatcharr_settings.epg_id:
            update_progress("dispatcharr", 96, "Refreshing Dispatcharr EPG...")
            from teamarr.dispatcharr.factory import DispatcharrConnection

            raw_client = (
                dispatcharr_client.client
                if isinstance(dispatcharr_client, DispatcharrConnection)
                else dispatcharr_client
            )
            epg_manager = EPGManager(raw_client)
            # Increased timeout from 60s to 120s for large EPGs
            refresh_result = epg_manager.wait_for_refresh(dispatcharr_settings.epg_id, timeout=120)
            result.epg_refresh = {
                "success": refresh_result.success,
                "message": refresh_result.message,
                "duration": refresh_result.duration,
            }

            update_progress("dispatcharr", 97, "Associating EPG with channels...")
            result.epg_association = lifecycle_service.associate_epg_with_channels(
                dispatcharr_settings.epg_id
            )

        # Step 6: Process scheduled deletions (98-99%)
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

        # Step 7: Run reconciliation + cleanup (99-100%)
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

        # Cleanup old history (part of step 7)
        update_progress("cleanup", 99, "Cleaning up history...")
        try:
            with db_factory() as conn:
                cleanup_settings = get_reconciliation_settings(conn)
                retention_days = cleanup_settings.get("channel_history_retention_days", 90)
                deleted_count = cleanup_old_history(conn, retention_days)
                result.cleanup = {"deleted_count": deleted_count}
                if deleted_count > 0:
                    logger.info("[CLEANUP] Removed %d old history record(s)", deleted_count)
        except Exception as e:
            logger.warning("[CLEANUP] History cleanup failed: %s", e)
            result.cleanup = {"error": str(e)}

        # Cleanup unused logos if enabled (part of step 7)
        try:
            from teamarr.database.settings import get_dispatcharr_settings

            with db_factory() as conn:
                dispatcharr_settings = get_dispatcharr_settings(conn)
            if dispatcharr_settings.cleanup_unused_logos and dispatcharr_client:
                update_progress("cleanup", 99, "Cleaning up unused logos...")
                cleanup_result = dispatcharr_client.logos.cleanup_unused()
                if cleanup_result.success:
                    logos_deleted = (
                        cleanup_result.data.get("deleted_count", 0)
                        if cleanup_result.data
                        else 0
                    )
                    result.logo_cleanup = {"deleted_count": logos_deleted}
                    if logos_deleted > 0:
                        logger.info("[CLEANUP] Removed %d unused logo(s)", logos_deleted)
                else:
                    logger.warning("[CLEANUP] Logo cleanup failed: %s", cleanup_result.error)
                    result.logo_cleanup = {"error": cleanup_result.error}
        except Exception as e:
            logger.warning("[CLEANUP] Logo cleanup failed: %s", e)
            result.logo_cleanup = {"error": str(e)}

        # Update stats run
        stats_run.programmes_total = result.programmes_total
        stats_run.programmes_events = team_result.total_events + group_result.total_events
        stats_run.programmes_pregame = team_result.total_pregame + group_result.total_pregame
        stats_run.programmes_postgame = team_result.total_postgame + group_result.total_postgame
        stats_run.programmes_idle = team_result.total_idle  # Event groups don't have idle
        stats_run.channels_created = group_result.total_channels_created
        # Combine scheduled deletions + group cleanup deletions
        stats_run.channels_deleted = channels_deleted_count + group_result.total_channels_deleted
        stats_run.xmltv_size_bytes = result.file_size
        # Aggregate stream stats from event groups into full_epg run
        stats_run.streams_fetched = group_result.total_streams_fetched
        stats_run.streams_matched = group_result.total_streams_matched
        stats_run.streams_unmatched = group_result.total_streams_unmatched
        stats_run.extra_metrics["teams_processed"] = result.teams_processed
        stats_run.extra_metrics["groups_processed"] = result.groups_processed
        stats_run.extra_metrics["file_written"] = result.file_written

        # Count total active managed channels
        from teamarr.database.channels import get_all_managed_channels

        with db_factory() as conn:
            active_channels = get_all_managed_channels(conn, include_deleted=False)
            stats_run.channels_active = len(active_channels)
            logger.info("[GENERATION] %d active managed channels", len(active_channels))

        stats_run.complete(status="completed")

        with db_factory() as conn:
            save_run(conn, stats_run)

        result.completed_at = time.time()
        result.duration_seconds = round(result.completed_at - result.started_at, 1)
        result.success = True

        update_progress("complete", 100, "Generation complete")

        # Flush the service cache to SQLite for immediate persistence
        from teamarr.services.sports_data import flush_shared_cache

        flushed = flush_shared_cache()
        if flushed > 0:
            logger.debug("[CACHE] Flushed %d entries to SQLite", flushed)

    except Exception as e:
        logger.exception("[GENERATION] Failed: %s", e)
        result.success = False
        result.error = str(e)
        result.completed_at = time.time()
        result.duration_seconds = round(result.completed_at - result.started_at, 1)

        # Save failed run
        try:
            stats_run.complete(status="failed", error=str(e))
            with db_factory() as conn:
                save_run(conn, stats_run)
        except Exception as save_err:
            logger.warning("[GENERATION] Failed to save failed run stats: %s", save_err)

    finally:
        # Always release the lock
        _generation_running = False
        _generation_lock.release()

    return result


def _refresh_m3u_accounts(db_factory: Callable[[], Any], dispatcharr_client: Any) -> dict:
    """Refresh M3U accounts for all event groups."""
    from teamarr.database.groups import get_all_groups
    from teamarr.dispatcharr import M3UManager

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
    from teamarr.dispatcharr.factory import DispatcharrConnection

    raw_client = (
        dispatcharr_client.client
        if isinstance(dispatcharr_client, DispatcharrConnection)
        else dispatcharr_client
    )
    m3u_manager = M3UManager(raw_client)
    batch_result = m3u_manager.refresh_multiple(
        list(account_ids),
        timeout=120,
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
