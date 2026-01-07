"""EPG generation endpoints."""

import json
import logging
import queue
import threading
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse

from teamarr.api.dependencies import get_sports_service
from teamarr.api.generation_status import (
    complete_generation,
    fail_generation,
    get_status,
    is_in_progress,
    start_generation,
    update_status,
)
from teamarr.api.models import (
    EPGGenerateRequest,
    EPGGenerateResponse,
    EventEPGRequest,
    EventSearchResult,
    MatchCorrectionRequest,
    MatchCorrectionResponse,
    MatchStats,
    StreamBatchMatchRequest,
    StreamBatchMatchResponse,
    StreamMatchResultModel,
)
from teamarr.consumers.matching import StreamMatcher
from teamarr.database import get_db
from teamarr.services import (
    EventEPGOptions,
    SportsDataService,
    create_epg_service,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# EPG Generation endpoints
# =============================================================================


@router.post("/epg/generate", response_model=EPGGenerateResponse)
def generate_epg(
    request: EPGGenerateRequest,
    service: SportsDataService = Depends(get_sports_service),
):
    """Generate full EPG using the unified generation workflow.

    This endpoint calls run_full_generation() which handles:
    - M3U refresh
    - Team and event group processing
    - XMLTV generation and file output
    - Dispatcharr integration
    - Channel lifecycle (deletions, reconciliation, cleanup)

    For real-time progress, use GET /epg/generate/stream instead.
    """
    from teamarr.consumers.generation import run_full_generation
    from teamarr.database.settings import get_dispatcharr_settings
    from teamarr.dispatcharr import get_dispatcharr_connection

    # Get Dispatcharr connection if configured (not just client)
    # Must use get_dispatcharr_connection() to get DispatcharrConnection
    # with .m3u, .channels, .epg managers
    with get_db() as conn:
        dispatcharr_settings = get_dispatcharr_settings(conn)

    dispatcharr_client = None
    if dispatcharr_settings.enabled and dispatcharr_settings.url:
        dispatcharr_client = get_dispatcharr_connection(get_db)

    # Run unified generation
    result = run_full_generation(
        db_factory=get_db,
        dispatcharr_client=dispatcharr_client,
        progress_callback=None,
    )

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=result.error or "Generation failed",
        )

    return EPGGenerateResponse(
        programmes_count=result.programmes_total,
        teams_processed=result.teams_processed,
        events_processed=result.groups_programmes,
        duration_seconds=result.duration_seconds,
        run_id=result.run_id,
        match_stats=MatchStats(
            streams_fetched=0,
            streams_filtered=0,
            streams_eligible=0,
            streams_matched=0,
            streams_unmatched=0,
            streams_cached=0,
            match_rate=0.0,
        ),
    )


@router.get("/epg/generate/status")
def get_generation_status():
    """Get current EPG generation status for polling-based progress.

    Returns JSON with:
    - in_progress: bool - whether generation is running
    - status: str - current status (starting, progress, complete, error, idle)
    - message: str - human-readable message
    - percent: int - progress percentage (0-100)
    - phase: str - current phase (teams, groups, saving)
    - current: int - current item number
    - total: int - total items in current phase
    - item_name: str - name of current item being processed
    """
    return get_status()


@router.get("/epg/generate/stream")
def generate_epg_stream():
    """Stream EPG generation progress using Server-Sent Events.

    This endpoint calls the unified run_full_generation() function which
    handles the complete EPG workflow including:
    - M3U refresh
    - Team and event group processing
    - XMLTV generation and file output
    - Dispatcharr integration (EPG refresh, channel association)
    - Channel lifecycle (scheduled deletions)
    - Reconciliation (detect issues)
    - History cleanup
    """
    from teamarr.consumers.generation import run_full_generation
    from teamarr.database.settings import get_dispatcharr_settings
    from teamarr.dispatcharr import get_dispatcharr_connection

    # Check if already in progress
    if is_in_progress():
        err = {"status": "error", "message": "Generation already in progress"}
        return StreamingResponse(
            iter([f"data: {json.dumps(err)}\n\n"]),
            media_type="text/event-stream",
        )

    # Mark as started
    if not start_generation():
        err = {"status": "error", "message": "Failed to start generation"}
        return StreamingResponse(
            iter([f"data: {json.dumps(err)}\n\n"]),
            media_type="text/event-stream",
        )

    # Queue for progress updates
    progress_queue: queue.Queue = queue.Queue()

    def generate():
        """Generator function for SSE stream."""

        def run_generation():
            """Run EPG generation in background thread."""
            try:
                # Get Dispatcharr connection if configured (not just client)
                with get_db() as conn:
                    dispatcharr_settings = get_dispatcharr_settings(conn)

                dispatcharr_client = None
                if dispatcharr_settings.enabled and dispatcharr_settings.url:
                    dispatcharr_client = get_dispatcharr_connection(get_db)

                # Progress callback that updates status and queues for SSE
                def progress_callback(
                    phase: str,
                    percent: int,
                    message: str,
                    current: int,
                    total: int,
                    item_name: str,
                ):
                    update_status(
                        status="progress",
                        phase=phase,
                        percent=percent,
                        message=message,
                        current=current,
                        total=total,
                        item_name=item_name,
                    )
                    progress_queue.put(get_status())

                # Run unified generation
                result = run_full_generation(
                    db_factory=get_db,
                    dispatcharr_client=dispatcharr_client,
                    progress_callback=progress_callback,
                )

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

                progress_queue.put(get_status())

            except Exception as e:
                fail_generation(str(e))
                progress_queue.put(get_status())

            finally:
                progress_queue.put({"_done": True})

        # Start generation thread
        generation_thread = threading.Thread(target=run_generation, daemon=True)
        generation_thread.start()

        # Stream progress updates
        while True:
            try:
                data = progress_queue.get(timeout=0.5)

                if data.get("_done"):
                    break

                yield f"data: {json.dumps(data)}\n\n"

            except queue.Empty:
                # Send heartbeat to keep connection alive
                yield ": heartbeat\n\n"

        # Wait for thread to complete
        generation_thread.join(timeout=5)

        # Send final status
        yield f"data: {json.dumps(get_status())}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/epg/xmltv")
def get_xmltv():
    """Serve the most recently generated EPG file.

    Returns the combined XMLTV file from the last EPG generation.
    Use /epg/generate to create/update the EPG.

    Dispatcharr EPG source URL: http://teamarr:9195/api/v1/epg/xmltv
    """
    from pathlib import Path

    from fastapi.responses import FileResponse

    from teamarr.database.settings import get_epg_settings

    with get_db() as conn:
        epg_settings = get_epg_settings(conn)

    output_path = epg_settings.epg_output_path or "./teamarr.xml"
    file_path = Path(output_path)

    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="EPG file not found. Run EPG generation first.",
        )

    return FileResponse(
        path=file_path,
        media_type="application/xml",
        filename="teamarr.xml",
    )


# =============================================================================
# Event-based EPG endpoints
# =============================================================================


def _parse_date(date_str: str | None) -> date:
    """Parse date string or return today."""
    if not date_str:
        return date.today()
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid date format. Use YYYY-MM-DD.",
        ) from None


@router.post("/epg/events/generate", response_model=EPGGenerateResponse)
def generate_event_epg(
    request: EventEPGRequest,
    service: SportsDataService = Depends(get_sports_service),
):
    """Generate event-based EPG. Each event gets its own channel."""
    epg_service = create_epg_service(service)
    target = _parse_date(request.target_date)

    options = EventEPGOptions(
        pregame_minutes=request.pregame_minutes,
    )

    result = epg_service.generate_event_epg(
        request.leagues, target, request.channel_prefix, options
    )

    return EPGGenerateResponse(
        programmes_count=len(result.programmes),
        teams_processed=0,
        events_processed=result.events_processed,
        duration_seconds=(result.completed_at - result.started_at).total_seconds(),
    )


@router.get("/epg/events/xmltv")
def get_event_xmltv(
    leagues: str = Query(..., description="Comma-separated league codes"),
    target_date: str | None = Query(None, description="Date (YYYY-MM-DD)"),
    channel_prefix: str = Query("event"),
    pregame_minutes: int = Query(0, ge=0, le=120),
    duration_hours: float = Query(3.0, ge=1.0, le=8.0),
    service: SportsDataService = Depends(get_sports_service),
):
    """Get XMLTV for event-based EPG. Each event gets its own channel."""
    league_list = [x.strip() for x in leagues.split(",") if x.strip()]
    if not league_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one league required",
        )

    epg_service = create_epg_service(service)
    target = _parse_date(target_date)

    options = EventEPGOptions(
        pregame_minutes=pregame_minutes,
    )

    result = epg_service.generate_event_epg(league_list, target, channel_prefix, options)

    return Response(
        content=result.xmltv,
        media_type="application/xml",
        headers={"Content-Disposition": "inline; filename=teamarr-events.xml"},
    )


# =============================================================================
# Stream Matching (with fingerprint cache)
# =============================================================================


@router.post("/epg/streams/match", response_model=StreamBatchMatchResponse)
def match_streams(
    request: StreamBatchMatchRequest,
    service: SportsDataService = Depends(get_sports_service),
):
    """Match streams to events using fingerprint cache.

    On cache hit: returns cached event, skips expensive matching.
    On cache miss: performs full match, caches result.

    Fingerprint = hash(group_id + stream_id + stream_name).
    If stream name changes, fingerprint changes -> fresh match.
    """
    target = _parse_date(request.target_date)

    # Create stream matcher for this group
    matcher = StreamMatcher(
        service=service,
        db_factory=get_db,
        group_id=request.group_id,
        search_leagues=request.search_leagues,
        include_leagues=request.include_leagues,
    )

    # Convert input to dicts
    streams = [{"id": s.id, "name": s.name} for s in request.streams]

    # Match all streams (uses cache where possible)
    batch_result = matcher.match_all(streams, target)

    # Purge stale cache entries
    matcher.purge_stale()

    # Build response
    results = []
    for r in batch_result.results:
        result_model = StreamMatchResultModel(
            stream_name=r.stream_name,
            matched=r.matched,
            event_id=r.event.id if r.event else None,
            event_name=r.event.name if r.event else None,
            league=r.league,
            home_team=r.event.home_team.name if r.event else None,
            away_team=r.event.away_team.name if r.event else None,
            start_time=r.event.start_time.isoformat() if r.event else None,
            included=r.included,
            exclusion_reason=r.exclusion_reason,
            from_cache=r.from_cache,
        )
        results.append(result_model)

    # Calculate match rate
    non_exception = batch_result.total
    match_rate = batch_result.matched_count / non_exception if non_exception > 0 else 0.0

    return StreamBatchMatchResponse(
        total=batch_result.total,
        matched=batch_result.matched_count,
        included=batch_result.included_count,
        unmatched=batch_result.unmatched_count,
        match_rate=match_rate,
        cache_hits=batch_result.cache_hits,
        cache_misses=batch_result.cache_misses,
        cache_hit_rate=batch_result.cache_hit_rate,
        results=results,
    )


# =============================================================================
# EPG Analysis
# =============================================================================


def _get_combined_xmltv() -> str:
    """Get combined XMLTV content from all teams."""
    with get_db() as conn:
        rows = conn.execute("SELECT xmltv_content FROM team_epg_xmltv ORDER BY team_id").fetchall()

    if not rows:
        return ""

    # Merge all XMLTV content
    from teamarr.utilities.xmltv import merge_xmltv_content

    contents = [row["xmltv_content"] for row in rows if row["xmltv_content"]]
    if not contents:
        return ""

    return merge_xmltv_content(contents)


def _analyze_xmltv(xmltv_content: str) -> dict:
    """Analyze XMLTV content for issues."""
    import re
    import xml.etree.ElementTree as ET
    from collections import defaultdict

    result = {
        "channels": {"total": 0, "team_based": 0, "event_based": 0},
        "programmes": {
            "total": 0,
            "events": 0,
            "pregame": 0,
            "postgame": 0,
            "idle": 0,
        },
        "date_range": {"start": None, "end": None},
        "unreplaced_variables": [],
        "coverage_gaps": [],
    }

    if not xmltv_content:
        return result

    try:
        # Parse with comments
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        root = ET.fromstring(xmltv_content, parser=parser)
    except ET.ParseError:
        return result

    # Count channels
    channels = root.findall("channel")
    result["channels"]["total"] = len(channels)
    for ch in channels:
        ch_id = ch.get("id", "")
        if ch_id.startswith("teamarr-event-"):
            result["channels"]["event_based"] += 1
        else:
            result["channels"]["team_based"] += 1

    # Analyze programmes
    programmes = root.findall("programme")
    result["programmes"]["total"] = len(programmes)

    # Track programmes per channel for gap detection
    channel_programmes: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    unreplaced_vars: set[str] = set()
    var_pattern = re.compile(r"\{[a-z_]+\}")

    min_start = None
    max_stop = None

    for prog in programmes:
        channel_id = prog.get("channel", "")
        start = prog.get("start", "")
        stop = prog.get("stop", "")

        # Track date range
        if start:
            start_date = start[:8]
            if min_start is None or start_date < min_start:
                min_start = start_date
        if stop:
            stop_date = stop[:8]
            if max_stop is None or stop_date > max_stop:
                max_stop = stop_date

        # Get text content for variable checking
        title = prog.findtext("title", "") or ""
        subtitle = prog.findtext("sub-title", "") or ""
        desc = prog.findtext("desc", "") or ""

        # Check for programme type from filler comment (V1 compatibility)
        # Comments look like: <!-- teamarr:filler-pregame -->
        filler_type = None
        for child in prog:
            if callable(child.tag):  # This is a Comment
                comment_text = child.text or ""
                if comment_text.startswith("teamarr:filler-"):
                    filler_type = comment_text.replace("teamarr:filler-", "")
                    break

        if filler_type == "pregame":
            result["programmes"]["pregame"] += 1
        elif filler_type == "postgame":
            result["programmes"]["postgame"] += 1
        elif filler_type == "idle":
            result["programmes"]["idle"] += 1
        else:
            result["programmes"]["events"] += 1

        for text in [title, subtitle, desc]:
            if text:
                matches = var_pattern.findall(text)
                unreplaced_vars.update(matches)

        # Store for gap detection
        if channel_id and start and stop:
            channel_programmes[channel_id].append((start, stop, title or "Unknown"))

    result["unreplaced_variables"] = sorted(unreplaced_vars)
    result["date_range"]["start"] = min_start
    result["date_range"]["end"] = max_stop

    # Detect coverage gaps (> 5 minute gap between programmes)
    from datetime import datetime

    for channel_id, progs in channel_programmes.items():
        # Sort by start time
        progs.sort(key=lambda x: x[0])

        for i in range(len(progs) - 1):
            _, stop1, title1 = progs[i]
            start2, _, title2 = progs[i + 1]

            # Parse times (format: YYYYMMDDHHmmss +ZZZZ)
            try:
                stop1_time = stop1[:14]
                start2_time = start2[:14]

                fmt = "%Y%m%d%H%M%S"
                dt_stop = datetime.strptime(stop1_time, fmt)
                dt_start = datetime.strptime(start2_time, fmt)

                gap_seconds = (dt_start - dt_stop).total_seconds()
                gap_minutes = int(gap_seconds / 60)

                if gap_minutes > 5:  # More than 5 minute gap
                    result["coverage_gaps"].append(
                        {
                            "channel": channel_id,
                            "after_program": title1[:50],
                            "before_program": title2[:50],
                            "after_stop": stop1,
                            "before_start": start2,
                            "gap_minutes": gap_minutes,
                        }
                    )
            except (ValueError, TypeError):
                continue

    return result


@router.get("/epg/analysis")
def get_epg_analysis():
    """Analyze current EPG for issues.

    Returns:
    - Channel counts (team vs event based)
    - Programme counts by type (events, pregame, postgame, idle) from latest run
    - Date range coverage
    - Unreplaced template variables
    - Coverage gaps between programmes
    """
    xmltv = _get_combined_xmltv()
    result = _analyze_xmltv(xmltv)

    # Override programme counts with stats from latest full_epg processing run
    # (XML comments may not survive serialization, so use DB stats instead)
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT programmes_total, programmes_events, programmes_pregame,
                   programmes_postgame, programmes_idle
            FROM processing_runs
            WHERE status = 'completed' AND run_type = 'full_epg'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row:
            result["programmes"]["total"] = row["programmes_total"] or result["programmes"]["total"]
            result["programmes"]["events"] = row["programmes_events"] or 0
            result["programmes"]["pregame"] = row["programmes_pregame"] or 0
            result["programmes"]["postgame"] = row["programmes_postgame"] or 0
            result["programmes"]["idle"] = row["programmes_idle"] or 0

        # Get actual managed channel count from database (not XMLTV)
        # This is more accurate as event channels may not be in XMLTV yet
        event_channel_count = conn.execute(
            "SELECT COUNT(*) FROM managed_channels WHERE deleted_at IS NULL"
        ).fetchone()[0]
        result["channels"]["event_based"] = event_channel_count
        result["channels"]["total"] = (
            result["channels"]["team_based"] + event_channel_count
        )

    return result


@router.get("/epg/content")
def get_epg_content(
    max_lines: int = Query(2000, ge=0, le=100000, description="Max lines to return (0 = all)"),
):
    """Get raw XMLTV content for preview.

    Returns the combined XMLTV content as text for display in UI.
    Use max_lines=0 to return the full content without truncation.
    """
    xmltv = _get_combined_xmltv()

    if not xmltv:
        return {"content": "", "total_lines": 0, "truncated": False, "size_bytes": 0}

    lines = xmltv.split("\n")
    total_lines = len(lines)

    # max_lines=0 means no limit
    if max_lines > 0 and total_lines > max_lines:
        truncated = True
        lines = lines[:max_lines]
    else:
        truncated = False

    return {
        "content": "\n".join(lines),
        "total_lines": total_lines,
        "truncated": truncated,
        "size_bytes": len(xmltv.encode("utf-8")),
    }


# =============================================================================
# Match stats endpoints
# =============================================================================


@router.get("/epg/matched-streams")
def get_matched_streams(
    run_id: int | None = Query(None, description="Processing run ID (defaults to latest)"),
    group_id: int | None = Query(None, description="Filter by event group ID"),
    limit: int = Query(500, ge=1, le=2000, description="Max results"),
):
    """Get matched streams from an EPG generation run.

    Returns list of streams that were successfully matched to events.
    """
    from teamarr.database.stats import get_matched_streams as db_get_matched

    with get_db() as conn:
        streams = db_get_matched(conn, run_id=run_id, group_id=group_id, limit=limit)

    return {
        "count": len(streams),
        "run_id": run_id,
        "group_id": group_id,
        "streams": streams,
    }


@router.get("/epg/failed-matches")
def get_failed_matches(
    run_id: int | None = Query(None, description="Processing run ID (defaults to latest)"),
    group_id: int | None = Query(None, description="Filter by event group ID"),
    reason: str | None = Query(None, description="Filter by failure reason"),
    limit: int = Query(500, ge=1, le=2000, description="Max results"),
):
    """Get failed matches from an EPG generation run.

    Returns list of streams that failed to match to events.

    Reasons:
    - unmatched: No event found for stream
    - excluded_league: Matched but event is in non-configured league
    - exception: Stream contains exception keyword
    """
    from teamarr.database.stats import get_failed_matches as db_get_failed

    with get_db() as conn:
        failures = db_get_failed(conn, run_id=run_id, group_id=group_id, reason=reason, limit=limit)

    return {
        "count": len(failures),
        "run_id": run_id,
        "group_id": group_id,
        "reason_filter": reason,
        "failures": failures,
    }


@router.get("/epg/match-stats")
def get_match_stats(
    run_id: int | None = Query(None, description="Processing run ID (defaults to latest)"),
):
    """Get match statistics summary for an EPG generation run.

    Returns:
    - Total matched/unmatched/cached counts
    - Match rate percentage
    - Breakdown by group and league
    - Failure reasons breakdown
    """
    from teamarr.database.stats import get_match_stats_summary

    with get_db() as conn:
        stats = get_match_stats_summary(conn, run_id=run_id)

    return stats


# =============================================================================
# Match Correction Endpoints (Phase 7)
# =============================================================================


@router.post("/epg/streams/correct", response_model=MatchCorrectionResponse)
def correct_stream_match(
    request: MatchCorrectionRequest,
    service: SportsDataService = Depends(get_sports_service),
):
    """Correct an incorrect or failed stream match.

    This creates a user-corrected entry in the cache that takes priority
    over algorithmic matching. User corrections are never auto-purged.

    Use correct_event_id=None to mark a stream as "no event" (explicit skip).
    """
    from teamarr.consumers.stream_match_cache import (
        StreamMatchCache,
        compute_fingerprint,
        event_to_cache_data,
    )

    cache = StreamMatchCache(get_db)

    # Get current cache entry if exists
    current = cache.get(
        group_id=request.group_id,
        stream_id=request.stream_id,
        stream_name=request.stream_name,
    )
    previous_event_id = current.event_id if current else None

    # If correcting to a specific event, fetch the event data
    cached_data = {}
    if request.correct_event_id and request.correct_league:
        # Try to find the event to cache its data
        events = service.get_events(request.correct_league, date.today())
        event = next((e for e in events if e.id == request.correct_event_id), None)
        if event:
            cached_data = event_to_cache_data(event)

    # Apply the correction
    success = cache.set_user_correction(
        group_id=request.group_id,
        stream_id=request.stream_id,
        stream_name=request.stream_name,
        event_id=request.correct_event_id,
        league=request.correct_league,
        cached_data=cached_data,
    )

    fingerprint = compute_fingerprint(request.group_id, request.stream_id, request.stream_name)

    return MatchCorrectionResponse(
        success=success,
        fingerprint=fingerprint,
        message="Correction applied" if success else "Failed to apply correction",
        previous_event_id=previous_event_id,
        new_event_id=request.correct_event_id,
    )


@router.delete("/epg/streams/correct/{group_id}/{stream_id}")
def remove_stream_correction(
    group_id: int,
    stream_id: int,
    stream_name: str = Query(..., description="Stream name for fingerprint"),
):
    """Remove a user correction, allowing the stream to be re-matched.

    This deletes the user-corrected cache entry. On next EPG generation,
    the stream will be matched algorithmically again.
    """
    from teamarr.consumers.stream_match_cache import StreamMatchCache, compute_fingerprint

    cache = StreamMatchCache(get_db)

    # Check if it's actually a user correction
    entry = cache.get(group_id, stream_id, stream_name)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No cache entry found for this stream",
        )

    if not entry.user_corrected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This entry is not a user correction",
        )

    # Remove the correction
    success = cache.remove_user_correction(group_id, stream_id, stream_name)

    fingerprint = compute_fingerprint(group_id, stream_id, stream_name)

    return {
        "success": success,
        "fingerprint": fingerprint,
        "message": "Correction removed" if success else "Failed to remove correction",
    }


@router.get("/epg/events/search")
def search_events(
    league: str | None = Query(None, description="Filter by league code"),
    team: str | None = Query(None, description="Search by team name"),
    target_date: str | None = Query(None, description="Target date (YYYY-MM-DD)"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
    service: SportsDataService = Depends(get_sports_service),
):
    """Search events for manual match correction UI.

    Returns events matching the search criteria. Use this to find the
    correct event when manually correcting a failed or incorrect match.
    """
    from teamarr.database.leagues import get_all_leagues

    target = _parse_date(target_date) if target_date else date.today()
    results: list[EventSearchResult] = []

    # Get league info for display names
    with get_db() as conn:
        all_leagues = {lg["league_code"]: lg for lg in get_all_leagues(conn)}

    # If league specified, search only that league
    if league:
        leagues_to_search = [league]
    else:
        # Search top leagues (limit to prevent too many API calls)
        leagues_to_search = ["nfl", "nba", "nhl", "mlb", "eng.1", "mls"]

    for lg in leagues_to_search:
        try:
            events = service.get_events(lg, target)
        except Exception:
            continue

        for event in events:
            # Filter by team name if specified
            if team:
                team_lower = team.lower()
                home_name = event.home_team.name.lower() if event.home_team else ""
                away_name = event.away_team.name.lower() if event.away_team else ""
                if team_lower not in home_name and team_lower not in away_name:
                    continue

            lg_info = all_leagues.get(lg, {})
            results.append(
                EventSearchResult(
                    event_id=event.id,
                    event_name=event.name,
                    league=lg,
                    league_name=lg_info.get("display_name"),
                    start_time=event.start_time.isoformat(),
                    home_team=event.home_team.name if event.home_team else None,
                    away_team=event.away_team.name if event.away_team else None,
                    status=event.status.state if event.status else None,
                )
            )

            if len(results) >= limit:
                break

        if len(results) >= limit:
            break

    return {
        "count": len(results),
        "target_date": target.isoformat(),
        "events": [r.model_dump() for r in results],
    }


@router.get("/epg/streams/corrections")
def list_user_corrections(
    group_id: int | None = Query(None, description="Filter by group ID"),
    limit: int = Query(100, ge=1, le=500, description="Max results"),
):
    """List all user-corrected stream matches.

    Returns streams where users have manually overridden the match.
    """
    with get_db() as conn:
        query = """
            SELECT fingerprint, group_id, stream_id, stream_name,
                   event_id, league, match_method, corrected_at
            FROM stream_match_cache
            WHERE user_corrected = 1
        """
        params: list = []

        if group_id is not None:
            query += " AND group_id = ?"
            params.append(group_id)

        query += " ORDER BY corrected_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()

    return {
        "count": len(rows),
        "corrections": [dict(row) for row in rows],
    }
