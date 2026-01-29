"""EPG generation status tracking.

Provides global state for tracking EPG generation progress,
used by both SSE streaming and polling endpoints.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Any


@dataclass
class GenerationStatus:
    """Current EPG generation status."""

    in_progress: bool = False
    status: str = "idle"  # idle, starting, teams, groups, saving, complete, error
    message: str = ""
    percent: int = 0
    phase: str = ""  # teams, groups, saving
    current: int = 0
    total: int = 0
    item_name: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "in_progress": self.in_progress,
            "status": self.status,
            "message": self.message,
            "percent": self.percent,
            "phase": self.phase,
            "current": self.current,
            "total": self.total,
            "item_name": self.item_name,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "result": self.result,
        }

    def reset(self) -> None:
        """Reset to idle state."""
        self.in_progress = False
        self.status = "idle"
        self.message = ""
        self.percent = 0
        self.phase = ""
        self.current = 0
        self.total = 0
        self.item_name = ""
        self.started_at = None
        self.completed_at = None
        self.error = None
        self.result = {}


# Global status instance with thread-safe access
_status = GenerationStatus()
_status_lock = Lock()


def get_status() -> dict:
    """Get current generation status as dict."""
    with _status_lock:
        return _status.to_dict()


def is_in_progress() -> bool:
    """Check if generation is in progress."""
    with _status_lock:
        return _status.in_progress


def start_generation() -> bool:
    """Mark generation as started.

    Returns False if already in progress.
    """
    with _status_lock:
        if _status.in_progress:
            return False
        _status.reset()
        _status.in_progress = True
        _status.status = "starting"
        _status.message = "Initializing EPG generation..."
        _status.percent = 0
        _status.started_at = datetime.now()
        return True


def update_status(
    status: str | None = None,
    message: str | None = None,
    percent: int | None = None,
    phase: str | None = None,
    current: int | None = None,
    total: int | None = None,
    item_name: str | None = None,
) -> None:
    """Update generation status.

    Progress percentage is monotonically increasing - once set to a value,
    it cannot go backwards. This prevents display glitches from race conditions.
    """
    with _status_lock:
        if status is not None:
            _status.status = status
        if message is not None:
            _status.message = message
        if percent is not None:
            # Never allow progress to go backwards
            if percent > _status.percent:
                _status.percent = percent
        if phase is not None:
            _status.phase = phase
        if current is not None:
            _status.current = current
        if total is not None:
            _status.total = total
        if item_name is not None:
            _status.item_name = item_name


def complete_generation(result: dict) -> None:
    """Mark generation as complete."""
    with _status_lock:
        _status.in_progress = False
        _status.status = "complete"
        _status.message = "EPG generation complete"
        _status.percent = 100
        _status.completed_at = datetime.now()
        _status.result = result


def fail_generation(error: str) -> None:
    """Mark generation as failed."""
    with _status_lock:
        _status.in_progress = False
        _status.status = "error"
        _status.message = f"Error: {error}"
        _status.error = error
        _status.completed_at = datetime.now()


def create_progress_callback(
    phase: str,
    phase_start_pct: int,
    phase_end_pct: int,
) -> Callable[[int, int, str], None]:
    """Create a progress callback for a specific phase.

    Args:
        phase: Phase name (teams, groups, saving)
        phase_start_pct: Starting percentage for this phase
        phase_end_pct: Ending percentage for this phase

    Returns:
        Callback function(current, total, item_name)
    """

    def callback(current: int, total: int, item_name: str) -> None:
        if total > 0:
            phase_progress = current / total
            percent = phase_start_pct + int(phase_progress * (phase_end_pct - phase_start_pct))
        else:
            percent = phase_start_pct

        update_status(
            status="progress",
            phase=phase,
            current=current,
            total=total,
            item_name=item_name,
            message=f"Processing {item_name} ({current}/{total})",
            percent=percent,
        )

    return callback
