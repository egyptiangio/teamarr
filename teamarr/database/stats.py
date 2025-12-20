"""Stats database operations.

Provides CRUD for processing_runs and stats_snapshots tables.
Centralized stats tracking for all processing operations.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from sqlite3 import Connection
from typing import Literal

logger = logging.getLogger(__name__)


# =============================================================================
# TYPES
# =============================================================================

RunType = Literal["event_group", "team_epg", "batch", "reconciliation", "scheduler"]
RunStatus = Literal["running", "completed", "failed", "partial"]


@dataclass
class ProcessingRun:
    """A processing run record."""

    id: int | None = None
    run_type: RunType = "event_group"
    run_id: str | None = None
    group_id: int | None = None
    team_id: int | None = None

    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    duration_ms: int | None = None
    status: RunStatus = "running"
    error_message: str | None = None

    # Stream metrics
    streams_fetched: int = 0
    streams_matched: int = 0
    streams_unmatched: int = 0
    streams_cached: int = 0

    # Channel metrics
    channels_created: int = 0
    channels_updated: int = 0
    channels_deleted: int = 0
    channels_skipped: int = 0
    channels_errors: int = 0

    # Programme metrics
    programmes_total: int = 0
    programmes_events: int = 0
    programmes_pregame: int = 0
    programmes_postgame: int = 0
    programmes_idle: int = 0

    xmltv_size_bytes: int = 0

    # Extensible metrics
    extra_metrics: dict = field(default_factory=dict)

    def complete(self, status: RunStatus = "completed", error: str | None = None):
        """Mark run as complete and calculate duration."""
        self.completed_at = datetime.now()
        self.status = status
        self.error_message = error
        if self.started_at:
            delta = self.completed_at - self.started_at
            self.duration_ms = int(delta.total_seconds() * 1000)

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "id": self.id,
            "run_type": self.run_type,
            "run_id": self.run_id,
            "group_id": self.group_id,
            "team_id": self.team_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error_message": self.error_message,
            "streams": {
                "fetched": self.streams_fetched,
                "matched": self.streams_matched,
                "unmatched": self.streams_unmatched,
                "cached": self.streams_cached,
            },
            "channels": {
                "created": self.channels_created,
                "updated": self.channels_updated,
                "deleted": self.channels_deleted,
                "skipped": self.channels_skipped,
                "errors": self.channels_errors,
            },
            "programmes": {
                "total": self.programmes_total,
                "events": self.programmes_events,
                "pregame": self.programmes_pregame,
                "postgame": self.programmes_postgame,
                "idle": self.programmes_idle,
            },
            "xmltv_size_bytes": self.xmltv_size_bytes,
            "extra_metrics": self.extra_metrics,
        }


@dataclass
class StatsSnapshot:
    """Aggregate stats snapshot."""

    id: int | None = None
    snapshot_type: str = "daily"
    period_start: datetime = field(default_factory=datetime.now)
    period_end: datetime = field(default_factory=datetime.now)

    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0

    total_streams_matched: int = 0
    total_streams_unmatched: int = 0
    total_channels_created: int = 0
    total_programmes_generated: int = 0

    programmes_by_type: dict = field(default_factory=dict)

    avg_duration_ms: int = 0
    max_duration_ms: int = 0

    extra_stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "id": self.id,
            "snapshot_type": self.snapshot_type,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "total_runs": self.total_runs,
            "successful_runs": self.successful_runs,
            "failed_runs": self.failed_runs,
            "total_streams_matched": self.total_streams_matched,
            "total_streams_unmatched": self.total_streams_unmatched,
            "total_channels_created": self.total_channels_created,
            "total_programmes_generated": self.total_programmes_generated,
            "programmes_by_type": self.programmes_by_type,
            "avg_duration_ms": self.avg_duration_ms,
            "max_duration_ms": self.max_duration_ms,
            "extra_stats": self.extra_stats,
        }


# =============================================================================
# PROCESSING RUNS CRUD
# =============================================================================


def create_run(
    conn: Connection,
    run_type: RunType,
    group_id: int | None = None,
    team_id: int | None = None,
) -> ProcessingRun:
    """Create a new processing run record.

    Returns a ProcessingRun with the database ID set.
    Call save_run() after processing to persist metrics.
    """
    run = ProcessingRun(
        run_type=run_type,
        run_id=str(uuid.uuid4()),
        group_id=group_id,
        team_id=team_id,
        started_at=datetime.now(),
    )

    cursor = conn.execute(
        """
        INSERT INTO processing_runs (
            run_type, run_id, group_id, team_id,
            started_at, status
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            run.run_type,
            run.run_id,
            run.group_id,
            run.team_id,
            run.started_at.isoformat(),
            run.status,
        ),
    )
    run.id = cursor.lastrowid
    conn.commit()

    return run


def save_run(conn: Connection, run: ProcessingRun) -> None:
    """Save/update a processing run with all metrics."""
    if run.id is None:
        raise ValueError("Run must have an ID (call create_run first)")

    conn.execute(
        """
        UPDATE processing_runs SET
            completed_at = ?,
            duration_ms = ?,
            status = ?,
            error_message = ?,
            streams_fetched = ?,
            streams_matched = ?,
            streams_unmatched = ?,
            streams_cached = ?,
            channels_created = ?,
            channels_updated = ?,
            channels_deleted = ?,
            channels_skipped = ?,
            channels_errors = ?,
            programmes_total = ?,
            programmes_events = ?,
            programmes_pregame = ?,
            programmes_postgame = ?,
            programmes_idle = ?,
            xmltv_size_bytes = ?,
            extra_metrics = ?
        WHERE id = ?
        """,
        (
            run.completed_at.isoformat() if run.completed_at else None,
            run.duration_ms,
            run.status,
            run.error_message,
            run.streams_fetched,
            run.streams_matched,
            run.streams_unmatched,
            run.streams_cached,
            run.channels_created,
            run.channels_updated,
            run.channels_deleted,
            run.channels_skipped,
            run.channels_errors,
            run.programmes_total,
            run.programmes_events,
            run.programmes_pregame,
            run.programmes_postgame,
            run.programmes_idle,
            run.xmltv_size_bytes,
            json.dumps(run.extra_metrics),
            run.id,
        ),
    )
    conn.commit()


def get_run(conn: Connection, run_id: int) -> ProcessingRun | None:
    """Get a processing run by ID."""
    row = conn.execute("SELECT * FROM processing_runs WHERE id = ?", (run_id,)).fetchone()

    if not row:
        return None

    return _row_to_run(dict(row))


def get_recent_runs(
    conn: Connection,
    limit: int = 50,
    run_type: RunType | None = None,
    group_id: int | None = None,
    status: RunStatus | None = None,
) -> list[ProcessingRun]:
    """Get recent processing runs with optional filters."""
    query = "SELECT * FROM processing_runs WHERE 1=1"
    params = []

    if run_type:
        query += " AND run_type = ?"
        params.append(run_type)

    if group_id:
        query += " AND group_id = ?"
        params.append(group_id)

    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [_row_to_run(dict(row)) for row in rows]


def _row_to_run(row: dict) -> ProcessingRun:
    """Convert database row to ProcessingRun."""
    return ProcessingRun(
        id=row["id"],
        run_type=row["run_type"],
        run_id=row.get("run_id"),
        group_id=row.get("group_id"),
        team_id=row.get("team_id"),
        started_at=(datetime.fromisoformat(row["started_at"]) if row.get("started_at") else None),
        completed_at=(
            datetime.fromisoformat(row["completed_at"]) if row.get("completed_at") else None
        ),
        duration_ms=row.get("duration_ms"),
        status=row.get("status", "completed"),
        error_message=row.get("error_message"),
        streams_fetched=row.get("streams_fetched", 0),
        streams_matched=row.get("streams_matched", 0),
        streams_unmatched=row.get("streams_unmatched", 0),
        streams_cached=row.get("streams_cached", 0),
        channels_created=row.get("channels_created", 0),
        channels_updated=row.get("channels_updated", 0),
        channels_deleted=row.get("channels_deleted", 0),
        channels_skipped=row.get("channels_skipped", 0),
        channels_errors=row.get("channels_errors", 0),
        programmes_total=row.get("programmes_total", 0),
        programmes_events=row.get("programmes_events", 0),
        programmes_pregame=row.get("programmes_pregame", 0),
        programmes_postgame=row.get("programmes_postgame", 0),
        programmes_idle=row.get("programmes_idle", 0),
        xmltv_size_bytes=row.get("xmltv_size_bytes", 0),
        extra_metrics=json.loads(row.get("extra_metrics") or "{}"),
    )


# =============================================================================
# AGGREGATE STATS
# =============================================================================


def get_current_stats(conn: Connection) -> dict:
    """Get current aggregate stats (live, not from snapshot).

    This is the main stats endpoint - calculates everything on demand.
    """
    # Overall counts
    overall = conn.execute(
        """
        SELECT
            COUNT(*) as total_runs,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as successful,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
            SUM(streams_matched) as total_matched,
            SUM(streams_unmatched) as total_unmatched,
            SUM(streams_cached) as total_cached,
            SUM(channels_created) as total_channels_created,
            SUM(channels_deleted) as total_channels_deleted,
            SUM(programmes_total) as total_programmes,
            SUM(programmes_events) as total_events,
            SUM(programmes_pregame) as total_pregame,
            SUM(programmes_postgame) as total_postgame,
            SUM(programmes_idle) as total_idle,
            AVG(duration_ms) as avg_duration,
            MAX(duration_ms) as max_duration
        FROM processing_runs
        """
    ).fetchone()

    # Last 24 hours
    yesterday = (datetime.now() - timedelta(days=1)).isoformat()
    last_24h = conn.execute(
        """
        SELECT
            COUNT(*) as runs,
            SUM(streams_matched) as matched,
            SUM(channels_created) as channels,
            SUM(programmes_total) as programmes
        FROM processing_runs
        WHERE created_at > ?
        """,
        (yesterday,),
    ).fetchone()

    # By run type
    by_type = {}
    type_rows = conn.execute(
        """
        SELECT run_type, COUNT(*) as count,
               SUM(programmes_total) as programmes
        FROM processing_runs
        GROUP BY run_type
        """
    ).fetchall()
    for row in type_rows:
        by_type[row["run_type"]] = {
            "runs": row["count"],
            "programmes": row["programmes"] or 0,
        }

    # Current managed channels count
    managed = conn.execute(
        """
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END) as active,
            SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END) as deleted
        FROM managed_channels
        """
    ).fetchone()

    return {
        "overall": {
            "total_runs": overall["total_runs"] or 0,
            "successful_runs": overall["successful"] or 0,
            "failed_runs": overall["failed"] or 0,
            "avg_duration_ms": int(overall["avg_duration"] or 0),
            "max_duration_ms": overall["max_duration"] or 0,
        },
        "streams": {
            "total_matched": overall["total_matched"] or 0,
            "total_unmatched": overall["total_unmatched"] or 0,
            "total_cached": overall["total_cached"] or 0,
            "cache_hit_rate": (
                round(overall["total_cached"] / overall["total_matched"] * 100, 1)
                if overall["total_matched"]
                else 0
            ),
        },
        "channels": {
            "total_created": overall["total_channels_created"] or 0,
            "total_deleted": overall["total_channels_deleted"] or 0,
            "currently_active": managed["active"] or 0,
            "currently_deleted": managed["deleted"] or 0,
        },
        "programmes": {
            "total": overall["total_programmes"] or 0,
            "events": overall["total_events"] or 0,
            "pregame": overall["total_pregame"] or 0,
            "postgame": overall["total_postgame"] or 0,
            "idle": overall["total_idle"] or 0,
        },
        "last_24h": {
            "runs": last_24h["runs"] or 0,
            "streams_matched": last_24h["matched"] or 0,
            "channels_created": last_24h["channels"] or 0,
            "programmes_generated": last_24h["programmes"] or 0,
        },
        "by_run_type": by_type,
    }


def get_stats_history(
    conn: Connection,
    days: int = 7,
    run_type: RunType | None = None,
) -> list[dict]:
    """Get daily stats history for charting."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    query = """
        SELECT
            DATE(created_at) as date,
            COUNT(*) as runs,
            SUM(streams_matched) as matched,
            SUM(streams_unmatched) as unmatched,
            SUM(channels_created) as channels,
            SUM(programmes_total) as programmes,
            AVG(duration_ms) as avg_duration
        FROM processing_runs
        WHERE created_at > ?
    """
    params = [cutoff]

    if run_type:
        query += " AND run_type = ?"
        params.append(run_type)

    query += " GROUP BY DATE(created_at) ORDER BY date"

    rows = conn.execute(query, params).fetchall()

    return [
        {
            "date": row["date"],
            "runs": row["runs"],
            "streams_matched": row["matched"] or 0,
            "streams_unmatched": row["unmatched"] or 0,
            "channels_created": row["channels"] or 0,
            "programmes_generated": row["programmes"] or 0,
            "avg_duration_ms": int(row["avg_duration"] or 0),
        }
        for row in rows
    ]


def cleanup_old_runs(conn: Connection, days: int = 30) -> int:
    """Delete processing runs older than specified days."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    cursor = conn.execute("DELETE FROM processing_runs WHERE created_at < ?", (cutoff,))
    conn.commit()
    return cursor.rowcount
