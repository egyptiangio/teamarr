"""Background scheduler for EPG generation.

Uses cron expressions for scheduling (like V1).
Runs periodic EPG generation using the unified run_full_generation() function
which handles everything:
- EPG generation (teams, groups, XMLTV)
- Dispatcharr integration
- Channel lifecycle (deletions, reconciliation, cleanup)

Integrates with FastAPI lifespan for clean startup/shutdown.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any

from croniter import croniter

logger = logging.getLogger(__name__)


class CronScheduler:
    """Background scheduler using cron expressions.

    Runs tasks at times specified by a cron expression.

    Usage:
        scheduler = CronScheduler(
            db_factory=get_db,
            cron_expression="0 * * * *",  # Every hour
        )
        scheduler.start()
        # ... application runs ...
        scheduler.stop()

    FastAPI integration:
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            scheduler = CronScheduler(get_db, "0 * * * *")
            scheduler.start()
            yield
            scheduler.stop()
    """

    def __init__(
        self,
        db_factory: Any,
        cron_expression: str = "0 * * * *",
        dispatcharr_client: Any = None,
        run_on_start: bool = True,
    ):
        """Initialize the scheduler.

        Args:
            db_factory: Factory function returning database connection
            cron_expression: Cron expression (e.g., "0 * * * *" for hourly)
            dispatcharr_client: Optional DispatcharrClient for Dispatcharr operations
            run_on_start: Whether to run tasks immediately on start
        """
        self._db_factory = db_factory
        self._cron_expression = cron_expression
        self._dispatcharr_client = dispatcharr_client
        self._run_on_start = run_on_start

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._last_run: datetime | None = None
        self._next_run: datetime | None = None

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def last_run(self) -> datetime | None:
        """Get time of last task run."""
        return self._last_run

    @property
    def next_run(self) -> datetime | None:
        """Get time of next scheduled run."""
        return self._next_run

    @property
    def cron_expression(self) -> str:
        """Get the cron expression."""
        return self._cron_expression

    def start(self) -> bool:
        """Start the scheduler.

        Returns:
            True if started, False if already running
        """
        if self.is_running:
            logger.warning("[CRON] Scheduler already running")
            return False

        # Validate cron expression
        try:
            croniter(self._cron_expression)
        except (KeyError, ValueError) as e:
            logger.error("[CRON] Invalid expression '%s': %s", self._cron_expression, e)
            return False

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="cron-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("[CRON] Scheduler started: %s", self._cron_expression)
        return True

    def stop(self, timeout: float = 30.0) -> bool:
        """Stop the scheduler gracefully.

        Args:
            timeout: Maximum seconds to wait for thread to stop

        Returns:
            True if stopped, False if timeout
        """
        if not self.is_running:
            return True

        logger.debug("[CRON] Stopping scheduler...")
        self._stop_event.set()
        self._running = False

        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("[CRON] Scheduler thread did not stop in time")
                return False

        logger.info("[CRON] Scheduler stopped")
        return True

    def run_once(self) -> dict:
        """Run all scheduled tasks once (for testing/manual trigger).

        Returns:
            Dict with task results
        """
        return self._run_tasks()

    def _run_loop(self) -> None:
        """Main scheduler loop - runs in background thread."""
        # Run immediately on startup if configured
        if self._run_on_start:
            try:
                logger.info("[CRON] Running initial scheduled tasks")
                self._run_tasks()
            except Exception as e:
                logger.exception("[CRON] Error in initial run: %s", e)

        while not self._stop_event.is_set():
            # Calculate next run time
            cron = croniter(self._cron_expression, datetime.now())
            self._next_run = cron.get_next(datetime)

            wait_seconds = (self._next_run - datetime.now()).total_seconds()
            logger.debug(
                "[CRON] Next run: %s (%.0fs)",
                self._next_run.strftime("%Y-%m-%d %H:%M:%S"),
                wait_seconds,
            )

            # Wait until next run time (checking stop event every second)
            while wait_seconds > 0 and not self._stop_event.is_set():
                sleep_time = min(1.0, wait_seconds)
                time.sleep(sleep_time)
                wait_seconds = (self._next_run - datetime.now()).total_seconds()

            if self._stop_event.is_set():
                return

            # Run tasks
            try:
                logger.info("[CRON] Scheduled run triggered")
                self._run_tasks()
            except Exception as e:
                logger.exception("[CRON] Error in scheduled run: %s", e)

    def _run_tasks(self) -> dict:
        """Run all scheduled tasks.

        Tasks:
        - Daily cache refresh (team/league data from ESPN/TSDB)
        - EPG generation (teams, groups, XMLTV)
        - Dispatcharr integration
        - Channel lifecycle (deletions, reconciliation, cleanup)

        Returns:
            Dict with task results
        """
        self._last_run = datetime.now()
        results = {
            "started_at": self._last_run.isoformat(),
            "cache_refresh": {},
            "epg_generation": {},
        }

        # Daily cache refresh (only refreshes if > 1 day old)
        try:
            results["cache_refresh"] = self._task_refresh_cache()
        except Exception as e:
            logger.warning("[CRON] Cache refresh task failed: %s", e)
            results["cache_refresh"] = {"error": str(e)}

        try:
            # Single unified generation call - does everything
            results["epg_generation"] = self._task_generate_epg()
        except Exception as e:
            logger.warning("[CRON] EPG generation task failed: %s", e)
            results["epg_generation"] = {"error": str(e)}

        results["completed_at"] = datetime.now().isoformat()
        return results

    def _task_refresh_cache(self) -> dict:
        """Refresh team/league cache if stale (daily).

        Cache is also refreshed unconditionally on every startup and
        can be triggered manually via the UI. This scheduled check
        catches staleness for long-running instances that haven't
        restarted in over a day.

        Returns:
            Dict with refresh status
        """
        from teamarr.services import create_cache_service

        cache_service = create_cache_service(self._db_factory)
        refreshed = cache_service.refresh_if_needed(max_age_days=1)

        if refreshed:
            stats = cache_service.get_stats()
            logger.info(
                "[CRON] Daily cache refresh: %d leagues, %d teams",
                stats.leagues_count,
                stats.teams_count,
            )
            return {
                "refreshed": True,
                "leagues_count": stats.leagues_count,
                "teams_count": stats.teams_count,
            }
        else:
            logger.debug("[CRON] Cache refresh skipped: not stale")
            return {"refreshed": False, "reason": "Cache not stale yet"}

    def _task_generate_epg(self) -> dict:
        """Generate EPG using the unified generation workflow.

        Uses run_full_generation() which handles:
        - M3U refresh
        - Team and event group processing
        - XMLTV merging and file output
        - Dispatcharr integration
        - Channel lifecycle (deletions, reconciliation, cleanup)

        Returns:
            Dict with generation stats
        """
        from teamarr.api.generation_status import (
            complete_generation,
            fail_generation,
            start_generation,
            update_status,
        )
        from teamarr.consumers.generation import run_full_generation

        # Mark generation as started (enables UI polling)
        if not start_generation():
            logger.warning("[CRON] EPG generation skipped: already in progress")
            return {"success": False, "error": "Generation already in progress"}

        def progress_callback(
            phase: str,
            percent: int,
            message: str,
            current: int,
            total: int,
            item_name: str,
        ):
            """Update global status for UI polling."""
            update_status(
                status="progress",
                phase=phase,
                percent=percent,
                message=message,
                current=current,
                total=total,
                item_name=item_name,
            )

        # Get fresh Dispatcharr connection from factory
        # (stored reference may be stale if settings were updated)
        from teamarr.dispatcharr import get_dispatcharr_connection

        dispatcharr_client = get_dispatcharr_connection(self._db_factory)

        # Run the unified generation with progress tracking
        result = run_full_generation(
            db_factory=self._db_factory,
            dispatcharr_client=dispatcharr_client,
            progress_callback=progress_callback,
        )

        # Update global status on completion
        if result.success:
            complete_generation(
                {
                    "success": True,
                    "programmes_count": result.programmes_total,
                    "teams_processed": result.teams_processed,
                    "groups_processed": result.groups_processed,
                    "duration_seconds": result.duration_seconds,
                    "run_id": result.run_id,
                }
            )
        else:
            fail_generation(result.error or "Unknown error")

        # Convert to dict format for backward compatibility
        return {
            "success": result.success,
            "error": result.error,
            "programmes_generated": result.programmes_total,
            "teams_processed": result.teams_processed,
            "teams_programmes": result.teams_programmes,
            "groups_processed": result.groups_processed,
            "groups_programmes": result.groups_programmes,
            "file_written": result.file_written,
            "file_path": result.file_path,
            "file_size": result.file_size,
            "duration_seconds": result.duration_seconds,
            "m3u_refresh": result.m3u_refresh,
            "epg_refresh": result.epg_refresh,
            "epg_association": result.epg_association,
            "deletions": result.deletions,
            "reconciliation": result.reconciliation,
            "cleanup": result.cleanup,
            "run_id": result.run_id,
        }


# =============================================================================
# MODULE-LEVEL FUNCTIONS
# =============================================================================

# Keep old name for backward compatibility
LifecycleScheduler = CronScheduler

_scheduler: CronScheduler | None = None


def start_lifecycle_scheduler(
    db_factory: Any,
    cron_expression: str | None = None,
    dispatcharr_client: Any = None,
) -> bool:
    """Start the global cron scheduler.

    Args:
        db_factory: Factory function returning database connection
        cron_expression: Cron expression (None = use settings)
        dispatcharr_client: Optional DispatcharrClient instance

    Returns:
        True if started, False if already running or disabled
    """
    global _scheduler

    from teamarr.database.settings import get_epg_settings, get_scheduler_settings

    # Get settings
    with db_factory() as conn:
        scheduler_settings = get_scheduler_settings(conn)
        epg_settings = get_epg_settings(conn)

    if not scheduler_settings.enabled:
        logger.info("[CRON] Scheduler disabled in settings")
        return False

    # Use provided cron expression or fall back to settings
    cron = cron_expression or epg_settings.cron_expression or "0 * * * *"

    if _scheduler and _scheduler.is_running:
        logger.warning("[CRON] Scheduler already running")
        return False

    _scheduler = CronScheduler(
        db_factory=db_factory,
        cron_expression=cron,
        dispatcharr_client=dispatcharr_client,
        run_on_start=False,  # Don't run EPG generation on startup
    )
    return _scheduler.start()


def stop_lifecycle_scheduler(timeout: float = 30.0) -> bool:
    """Stop the global cron scheduler.

    Args:
        timeout: Maximum seconds to wait

    Returns:
        True if stopped
    """
    global _scheduler

    if not _scheduler:
        return True

    result = _scheduler.stop(timeout)
    _scheduler = None
    return result


def is_scheduler_running() -> bool:
    """Check if the global scheduler is running."""
    return _scheduler is not None and _scheduler.is_running


def get_scheduler_status() -> dict:
    """Get status of the global scheduler."""
    if not _scheduler:
        return {"running": False}

    return {
        "running": _scheduler.is_running,
        "cron_expression": _scheduler.cron_expression,
        "last_run": _scheduler.last_run.isoformat() if _scheduler.last_run else None,
        "next_run": _scheduler.next_run.isoformat() if _scheduler.next_run else None,
    }
