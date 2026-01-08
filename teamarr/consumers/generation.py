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
    epg_refresh: dict = field(default_factory=dict)
    epg_association: dict = field(default_factory=dict)
    deletions: dict = field(default_factory=dict)
    reconciliation: dict = field(default_factory=dict)
    cleanup: dict = field(default_factory=dict)

    # For stats run tracking
    run_id: int | None = None


# Type alias for progress callback
# (phase: str, percent: int, message: str, current: int, total: int, item_name: str) -> None
ProgressCallback = Callable[[str, int, str, int, int, str], None]


def run_full_generation(
    db_factory: Callable[[], Any],
    dispatcharr_client: Any | None = None,
    progress_callback: ProgressCallback | None = None,
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
        logger.warning("EPG generation already in progress, skipping duplicate run")
        result = GenerationResult()
        result.success = False
        result.error = "Generation already in progress"
        return result

    if _generation_running:
        _generation_lock.release()
        logger.warning("EPG generation already in progress (flag check), skipping")
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
    from teamarr.database.settings import get_dispatcharr_settings, get_display_settings, get_epg_settings
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

    # Create stats run for tracking
    with db_factory() as conn:
        stats_run = create_run(conn, run_type="full_epg")
        result.run_id = stats_run.id

    try:
        # Increment generation counter ONCE at start of full EPG run
        # This ensures all groups in this run share the same generation
        from teamarr.consumers.stream_match_cache import increment_generation_counter

        current_generation = increment_generation_counter(db_factory)
        logger.info(f"EPG generation starting with cache generation {current_generation}")

        # Get settings
        with db_factory() as conn:
            settings = get_epg_settings(conn)
            dispatcharr_settings = get_dispatcharr_settings(conn)
            display_settings = get_display_settings(conn)

        # Step 1: Refresh M3U accounts (0-5%)
        update_progress("init", 3, "Refreshing M3U accounts...")
        if dispatcharr_client:
            result.m3u_refresh = _refresh_m3u_accounts(db_factory, dispatcharr_client)

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

        # Transition message - teams done, starting groups
        logger.info("Sending transition message: teams -> groups")
        update_progress(
            "groups", 50,
            f"Teams complete ({result.teams_processed} processed), loading event groups...",
            0, 1, "Loading event groups..."
        )
        logger.info("Transition message sent")

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
                    msg = f"Finished {name} ({current}/{total}) - {remaining} remaining [{elapsed:.1f}s]"
                else:
                    msg = f"Finished {name} ({current}/{total}) [{elapsed:.1f}s]"
                update_progress("groups", pct, msg, current, total, name)

        group_result = process_all_event_groups(
            db_factory=db_factory,
            dispatcharr_client=dispatcharr_client,
            progress_callback=group_progress,
            generation=current_generation,  # Share generation across all groups
        )
        result.groups_processed = group_result.groups_processed
        result.groups_programmes = group_result.total_programmes
        result.programmes_total = result.teams_programmes + result.groups_programmes

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
            logger.info(f"EPG written to {output_path} ({result.file_size:,} bytes)")

        # Create lifecycle service once for steps 5-6
        sports_service = create_default_service()
        lifecycle_service = create_lifecycle_service(
            db_factory,
            sports_service,
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
                logger.info(f"Deleted {channels_deleted_count} expired channel(s)")
        except Exception as e:
            logger.warning(f"Scheduled deletions failed: {e}")
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
                    logger.info(f"Reconciliation found {len(recon_result.issues_found)} issue(s)")
        except Exception as e:
            logger.warning(f"Reconciliation failed: {e}")
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
                    logger.info(f"Cleaned up {deleted_count} old history record(s)")
        except Exception as e:
            logger.warning(f"History cleanup failed: {e}")
            result.cleanup = {"error": str(e)}

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
            logger.info(f"EPG generation: {len(active_channels)} active managed channels")

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
            logger.debug(f"Flushed {flushed} cache entries to SQLite")

    except Exception as e:
        logger.exception(f"EPG generation failed: {e}")
        result.success = False
        result.error = str(e)
        result.completed_at = time.time()
        result.duration_seconds = round(result.completed_at - result.started_at, 1)

        # Save failed run
        try:
            stats_run.complete(status="failed", error=str(e))
            with db_factory() as conn:
                save_run(conn, stats_run)
        except Exception:
            pass

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
        skip_if_recent_minutes=60,
    )

    result["refreshed"] = batch_result.succeeded_count - batch_result.skipped_count
    result["skipped"] = batch_result.skipped_count
    result["failed"] = batch_result.failed_count
    result["duration"] = batch_result.duration

    if batch_result.succeeded_count > 0:
        logger.info(
            f"M3U refresh: {result['refreshed']} refreshed, "
            f"{result['skipped']} skipped (recently updated)"
        )

    return result
