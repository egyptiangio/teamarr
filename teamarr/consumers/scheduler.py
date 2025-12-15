"""Background scheduler for channel lifecycle tasks.

Runs periodic tasks:
- Process scheduled channel deletions
- Light reconciliation (detect and log issues)
- Cleanup old history records

Integrates with FastAPI lifespan for clean startup/shutdown.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class LifecycleScheduler:
    """Background scheduler for channel lifecycle tasks.

    Runs periodic tasks in a daemon thread:
    - Channel deletion based on scheduled times
    - Light reconciliation (detect-only)
    - History cleanup

    Usage:
        scheduler = LifecycleScheduler(
            db_factory=get_db,
            interval_minutes=15,
        )
        scheduler.start()
        # ... application runs ...
        scheduler.stop()

    FastAPI integration:
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            scheduler = LifecycleScheduler(get_db)
            scheduler.start()
            yield
            scheduler.stop()
    """

    def __init__(
        self,
        db_factory: Any,
        interval_minutes: int = 15,
        dispatcharr_client: Any = None,
    ):
        """Initialize the scheduler.

        Args:
            db_factory: Factory function returning database connection
            interval_minutes: Minutes between task runs
            dispatcharr_client: Optional DispatcharrClient for Dispatcharr operations
        """
        self._db_factory = db_factory
        self._interval_minutes = interval_minutes
        self._dispatcharr_client = dispatcharr_client

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._last_run: datetime | None = None

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def last_run(self) -> datetime | None:
        """Get time of last task run."""
        return self._last_run

    def start(self) -> bool:
        """Start the scheduler.

        Returns:
            True if started, False if already running
        """
        if self.is_running:
            logger.warning("Scheduler already running")
            return False

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="lifecycle-scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"Lifecycle scheduler started (interval: {self._interval_minutes} minutes)"
        )
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

        logger.info("Stopping lifecycle scheduler...")
        self._stop_event.set()
        self._running = False

        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("Scheduler thread did not stop in time")
                return False

        logger.info("Lifecycle scheduler stopped")
        return True

    def run_once(self) -> dict:
        """Run all scheduled tasks once (for testing/manual trigger).

        Returns:
            Dict with task results
        """
        return self._run_tasks()

    def _run_loop(self) -> None:
        """Main scheduler loop - runs in background thread."""
        interval_seconds = self._interval_minutes * 60

        # Run immediately on startup
        try:
            self._run_tasks()
        except Exception as e:
            logger.exception(f"Error in initial scheduler run: {e}")

        while not self._stop_event.is_set():
            # Wait for interval (checking stop event periodically)
            for _ in range(int(interval_seconds)):
                if self._stop_event.is_set():
                    return
                time.sleep(1)

            # Run tasks
            try:
                self._run_tasks()
            except Exception as e:
                logger.exception(f"Error in scheduler run: {e}")

    def _run_tasks(self) -> dict:
        """Run all scheduled tasks.

        Returns:
            Dict with task results
        """
        self._last_run = datetime.now()
        results = {
            "started_at": self._last_run.isoformat(),
            "deletions": {},
            "reconciliation": {},
            "cleanup": {},
        }

        try:
            # Task 1: Process scheduled deletions
            results["deletions"] = self._task_process_deletions()
        except Exception as e:
            logger.warning(f"Deletion task failed: {e}")
            results["deletions"] = {"error": str(e)}

        try:
            # Task 2: Light reconciliation (detect only)
            results["reconciliation"] = self._task_light_reconciliation()
        except Exception as e:
            logger.warning(f"Reconciliation task failed: {e}")
            results["reconciliation"] = {"error": str(e)}

        try:
            # Task 3: Cleanup old history
            results["cleanup"] = self._task_cleanup_history()
        except Exception as e:
            logger.warning(f"Cleanup task failed: {e}")
            results["cleanup"] = {"error": str(e)}

        results["completed_at"] = datetime.now().isoformat()
        return results

    def _task_process_deletions(self) -> dict:
        """Process channels past their scheduled delete time."""
        from teamarr.consumers import create_lifecycle_service

        service = create_lifecycle_service(
            self._db_factory,
            self._dispatcharr_client,
        )

        result = service.process_scheduled_deletions()

        if result.deleted:
            logger.info(f"Scheduler deleted {len(result.deleted)} expired channel(s)")

        return {
            "deleted_count": len(result.deleted),
            "error_count": len(result.errors),
        }

    def _task_light_reconciliation(self) -> dict:
        """Run detect-only reconciliation and log issues."""
        from teamarr.consumers import create_reconciler
        from teamarr.database.channels import get_reconciliation_settings

        # Check if reconciliation is enabled
        conn = self._db_factory()
        try:
            settings = get_reconciliation_settings(conn)
        finally:
            conn.close()

        if not settings.get("reconcile_on_epg_generation", True):
            return {"skipped": True, "reason": "disabled"}

        reconciler = create_reconciler(
            self._db_factory,
            self._dispatcharr_client,
        )

        # Detect only - don't auto-fix in background
        result = reconciler.reconcile(auto_fix=False)

        if result.issues_found:
            logger.info(
                f"Reconciliation found {len(result.issues_found)} issue(s): "
                f"{result.summary}"
            )

        return result.summary

    def _task_cleanup_history(self) -> dict:
        """Cleanup old channel history records."""
        from teamarr.database.channels import (
            cleanup_old_history,
            get_reconciliation_settings,
        )

        # Get retention days from settings
        conn = self._db_factory()
        try:
            settings = get_reconciliation_settings(conn)
            retention_days = settings.get("channel_history_retention_days", 90)
            deleted_count = cleanup_old_history(conn, retention_days)
            conn.commit()
        finally:
            conn.close()

        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old history record(s)")

        return {"deleted_count": deleted_count}


# =============================================================================
# MODULE-LEVEL FUNCTIONS
# =============================================================================


_scheduler: LifecycleScheduler | None = None


def start_lifecycle_scheduler(
    db_factory: Any,
    interval_minutes: int | None = None,
    dispatcharr_client: Any = None,
) -> bool:
    """Start the global lifecycle scheduler.

    Args:
        db_factory: Factory function returning database connection
        interval_minutes: Minutes between runs (None = use settings)
        dispatcharr_client: Optional DispatcharrClient instance

    Returns:
        True if started, False if already running or disabled
    """
    global _scheduler

    from teamarr.database.channels import get_scheduler_settings

    # Get settings
    conn = db_factory()
    try:
        settings = get_scheduler_settings(conn)
    finally:
        conn.close()

    if not settings.get("enabled", True):
        logger.info("Scheduler disabled in settings")
        return False

    interval = interval_minutes or settings.get("interval_minutes", 15)

    if _scheduler and _scheduler.is_running:
        logger.warning("Scheduler already running")
        return False

    _scheduler = LifecycleScheduler(
        db_factory=db_factory,
        interval_minutes=interval,
        dispatcharr_client=dispatcharr_client,
    )
    return _scheduler.start()


def stop_lifecycle_scheduler(timeout: float = 30.0) -> bool:
    """Stop the global lifecycle scheduler.

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
        "last_run": _scheduler.last_run.isoformat() if _scheduler.last_run else None,
        "interval_minutes": _scheduler._interval_minutes,
    }
