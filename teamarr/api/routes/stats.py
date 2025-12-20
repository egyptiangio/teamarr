"""Stats API endpoints.

Provides centralized access to all processing statistics:
- Current aggregate stats
- Historical run data
- Daily/weekly trends
"""

from fastapi import APIRouter, Query

from teamarr.database import get_db

router = APIRouter()


# =============================================================================
# CURRENT STATS
# =============================================================================


@router.get("")
def get_stats():
    """Get current aggregate stats.

    Returns all stats from a single endpoint:
    - Overall run counts and performance
    - Stream matching stats (matched, unmatched, cached)
    - Channel lifecycle stats (created, deleted, active)
    - Programme stats by type (events, pregame, postgame, idle)
    - Last 24 hour summary
    - Breakdown by run type
    """
    from teamarr.database.stats import get_current_stats

    with get_db() as conn:
        return get_current_stats(conn)


@router.get("/dashboard")
def get_dashboard_stats():
    """Get aggregated dashboard stats for UI quadrants.

    Returns stats organized for the Dashboard's 4 quadrants:
    - Teams: total, active, assigned, leagues breakdown
    - Event Groups: total, streams, match rates, leagues
    - EPG: channels, events, filler by type
    - Channels: active, logos, groups, deleted
    """
    import json

    with get_db() as conn:
        # Teams stats
        teams_cursor = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN template_id IS NOT NULL THEN 1 ELSE 0 END) as assigned
            FROM teams
        """)
        teams_row = teams_cursor.fetchone()

        # Teams by league
        leagues_cursor = conn.execute("""
            SELECT league, COUNT(*) as count
            FROM teams
            GROUP BY league
            ORDER BY count DESC
        """)
        team_leagues = [
            {"league": r["league"], "logo_url": None, "count": r["count"]}
            for r in leagues_cursor.fetchall()
        ]

        # Event groups stats
        groups_cursor = conn.execute("""
            SELECT
                id, name, leagues, total_stream_count
            FROM event_epg_groups
            WHERE enabled = 1
        """)
        groups = groups_cursor.fetchall()

        # Aggregate event group stats
        total_streams = 0
        matched_streams = 0
        event_leagues_set: set[str] = set()
        group_breakdown = []

        for g in groups:
            leagues = json.loads(g["leagues"]) if g["leagues"] else []
            event_leagues_set.update(leagues)
            stream_count = g["total_stream_count"] or 0
            total_streams += stream_count
            # For now, estimate matched as percentage (would need actual matching data)
            matched = stream_count  # Placeholder - would need actual match data
            matched_streams += matched
            group_breakdown.append(
                {
                    "name": g["name"],
                    "matched": matched,
                    "total": stream_count,
                }
            )

        event_leagues = [
            {"league": league, "logo_url": None, "count": 1} for league in sorted(event_leagues_set)
        ]

        # Managed channels stats
        channels_cursor = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN logo_url IS NOT NULL AND logo_url != ''
                    THEN 1 ELSE 0 END) as with_logos,
                SUM(CASE WHEN deleted_at IS NOT NULL
                    AND deleted_at > datetime('now', '-1 day')
                    THEN 1 ELSE 0 END) as deleted_24h
            FROM managed_channels
        """)
        channels_row = channels_cursor.fetchone()

        # Channel groups (from Dispatcharr - placeholder)
        channel_groups = 0
        channel_group_breakdown: list[dict] = []

        match_percent = 0
        if total_streams > 0:
            match_percent = round((matched_streams / total_streams) * 100)

        return {
            "teams": {
                "total": teams_row["total"] or 0,
                "active": teams_row["active"] or 0,
                "assigned": teams_row["assigned"] or 0,
                "leagues": team_leagues,
            },
            "event_groups": {
                "total": len(groups),
                "streams_total": total_streams,
                "streams_matched": matched_streams,
                "match_percent": match_percent,
                "leagues": event_leagues,
                "groups": group_breakdown,
            },
            "epg": {
                "channels_total": 0,
                "channels_team": 0,
                "channels_event": 0,
                "events_total": 0,
                "events_team": 0,
                "events_event": 0,
                "filler_total": 0,
                "filler_pregame": 0,
                "filler_postgame": 0,
                "filler_idle": 0,
                "programmes_total": 0,
            },
            "channels": {
                "active": channels_row["active"] or 0,
                "with_logos": channels_row["with_logos"] or 0,
                "groups": channel_groups,
                "deleted_24h": channels_row["deleted_24h"] or 0,
                "group_breakdown": channel_group_breakdown,
            },
        }


@router.get("/history")
def get_stats_history(
    days: int = Query(7, ge=1, le=90, description="Number of days of history"),
    run_type: str | None = Query(None, description="Filter by run type"),
):
    """Get daily stats history for charting.

    Returns per-day aggregates for the specified time range.
    """
    from teamarr.database.stats import get_stats_history as get_history

    with get_db() as conn:
        return get_history(conn, days=days, run_type=run_type)


# =============================================================================
# PROCESSING RUNS
# =============================================================================


@router.get("/runs")
def get_runs(
    limit: int = Query(50, ge=1, le=500, description="Max runs to return"),
    run_type: str | None = Query(None, description="Filter by run type"),
    group_id: int | None = Query(None, description="Filter by group ID"),
    status: str | None = Query(None, description="Filter by status"),
):
    """Get recent processing runs.

    Returns detailed information about recent processing runs
    with optional filtering.
    """
    from teamarr.database.stats import get_recent_runs

    with get_db() as conn:
        runs = get_recent_runs(
            conn,
            limit=limit,
            run_type=run_type,
            group_id=group_id,
            status=status,
        )
        return {
            "runs": [run.to_dict() for run in runs],
            "count": len(runs),
        }


@router.get("/runs/{run_id}")
def get_run(run_id: int):
    """Get a specific processing run by ID."""
    from fastapi import HTTPException, status

    from teamarr.database.stats import get_run as get_run_by_id

    with get_db() as conn:
        run = get_run_by_id(conn, run_id)
        if not run:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Run {run_id} not found",
            )
        return run.to_dict()


# =============================================================================
# MAINTENANCE
# =============================================================================


@router.delete("/runs/cleanup")
def cleanup_runs(
    days: int = Query(30, ge=1, le=365, description="Delete runs older than N days"),
):
    """Delete old processing runs.

    Cleans up historical run data to manage database size.
    """
    from teamarr.database.stats import cleanup_old_runs

    with get_db() as conn:
        deleted = cleanup_old_runs(conn, days=days)
        return {
            "deleted": deleted,
            "message": f"Deleted {deleted} runs older than {days} days",
        }
