"""Pattern learning status tracking.

Provides global state for tracking AI pattern learning progress,
similar to generation_status.py but for pattern learning tasks.
"""

from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Any


@dataclass
class PatternLearningStatus:
    """Current pattern learning status."""

    in_progress: bool = False
    status: str = "idle"  # idle, learning, complete, error, aborted
    message: str = ""
    percent: int = 0
    current_group: int = 0
    total_groups: int = 0
    current_group_name: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    abort_requested: bool = False

    # Results
    groups_completed: int = 0
    patterns_learned: int = 0
    avg_coverage: float = 0.0
    group_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        # Calculate ETA
        eta_seconds = None
        if self.in_progress and self.current_group > 0 and self.started_at:
            elapsed = (datetime.now() - self.started_at).total_seconds()
            rate = self.current_group / elapsed if elapsed > 0 else 0
            remaining = self.total_groups - self.current_group
            if rate > 0:
                eta_seconds = int(remaining / rate)

        return {
            "in_progress": self.in_progress,
            "status": self.status,
            "message": self.message,
            "percent": self.percent,
            "current_group": self.current_group,
            "total_groups": self.total_groups,
            "current_group_name": self.current_group_name,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "eta_seconds": eta_seconds,
            "groups_completed": self.groups_completed,
            "patterns_learned": self.patterns_learned,
            "avg_coverage": self.avg_coverage,
            "group_results": self.group_results,
        }

    def reset(self) -> None:
        """Reset to idle state."""
        self.in_progress = False
        self.status = "idle"
        self.message = ""
        self.percent = 0
        self.current_group = 0
        self.total_groups = 0
        self.current_group_name = ""
        self.started_at = None
        self.completed_at = None
        self.error = None
        self.abort_requested = False
        self.groups_completed = 0
        self.patterns_learned = 0
        self.avg_coverage = 0.0
        self.group_results = []


# Global status instance with thread-safe access
_status = PatternLearningStatus()
_status_lock = Lock()


def get_status() -> dict:
    """Get current pattern learning status as dict."""
    with _status_lock:
        return _status.to_dict()


def is_in_progress() -> bool:
    """Check if pattern learning is in progress."""
    with _status_lock:
        return _status.in_progress


def start_learning(total_groups: int) -> bool:
    """Mark pattern learning as started.

    Returns False if already in progress.
    """
    with _status_lock:
        if _status.in_progress:
            return False
        _status.reset()
        _status.in_progress = True
        _status.status = "learning"
        _status.message = "Starting pattern learning..."
        _status.percent = 0
        _status.total_groups = total_groups
        _status.started_at = datetime.now()
        return True


def update_progress(
    current_group: int,
    group_name: str,
    message: str | None = None,
) -> None:
    """Update learning progress."""
    with _status_lock:
        _status.current_group = current_group
        _status.current_group_name = group_name
        if _status.total_groups > 0:
            _status.percent = int((current_group / _status.total_groups) * 100)
        if message:
            _status.message = message


def add_group_result(result: dict) -> None:
    """Add a completed group result."""
    with _status_lock:
        _status.group_results.append(result)
        _status.groups_completed = len(_status.group_results)

        # Update aggregated stats
        successful = [r for r in _status.group_results if r.get("success")]
        _status.patterns_learned = sum(r.get("patterns_learned", 0) for r in _status.group_results)
        if successful:
            _status.avg_coverage = sum(r.get("coverage_percent", 0) for r in successful) / len(successful)


def complete_learning() -> None:
    """Mark pattern learning as complete."""
    with _status_lock:
        _status.in_progress = False
        _status.status = "complete"
        _status.message = f"Learned {_status.patterns_learned} patterns from {_status.groups_completed} groups"
        _status.percent = 100
        _status.completed_at = datetime.now()


def fail_learning(error: str) -> None:
    """Mark pattern learning as failed."""
    with _status_lock:
        _status.in_progress = False
        _status.status = "error"
        _status.message = f"Error: {error}"
        _status.error = error
        _status.completed_at = datetime.now()


def request_abort() -> bool:
    """Request learning abort.

    Returns True if abort was requested, False if not in progress.
    """
    with _status_lock:
        if not _status.in_progress:
            return False
        _status.abort_requested = True
        _status.message = "Abort requested, finishing current group..."
        return True


def is_abort_requested() -> bool:
    """Check if abort has been requested."""
    with _status_lock:
        return _status.abort_requested


def abort_learning() -> None:
    """Mark learning as aborted."""
    with _status_lock:
        _status.in_progress = False
        _status.status = "aborted"
        _status.message = "Pattern learning aborted by user"
        _status.error = "Aborted by user"
        _status.completed_at = datetime.now()
        _status.abort_requested = False
