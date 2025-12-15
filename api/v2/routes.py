"""V2 API Routes - Pure dataclass EPG generation.

Endpoints:
- POST /api/v2/generate/teams - Generate team-based EPG
- POST /api/v2/generate/events - Generate event-based EPG
- POST /api/v2/streams/match - Match streams to events
- GET /api/v2/events/<league>/<date> - Get events for league/date
"""

import logging
from datetime import date, datetime
from flask import Blueprint, jsonify, request

from consumers import (
    Orchestrator,
    TeamEPGGenerator,
    TeamEPGOptions,
    TeamChannelConfig,
    EventEPGGenerator,
    EventEPGOptions,
    EventTemplateConfig,
    MatchedStream,
    SingleLeagueMatcher,
    MultiLeagueMatcher,
    BatchMatchResult,
)
from core import Programme
from services import create_default_service
from utilities.xmltv import programmes_to_xmltv

logger = logging.getLogger(__name__)

bp = Blueprint("v2", __name__, url_prefix="/api/v2")


# -----------------------------------------------------------------------------
# Team-based EPG
# -----------------------------------------------------------------------------


@bp.route("/generate/teams", methods=["POST"])
def generate_teams_epg():
    """Generate team-based EPG using V2 pure dataclass pipeline.

    Request body (optional):
    {
        "days_ahead": 14,
        "output_days_ahead": 7,
        "timezone": "America/New_York"
    }

    Returns:
        JSON with XMLTV content and stats
    """
    try:
        data = request.get_json() or {}

        orchestrator = Orchestrator()

        options = TeamEPGOptions(
            schedule_days_ahead=data.get("days_ahead", 30),
            output_days_ahead=data.get("output_days_ahead", 14),
            epg_timezone=data.get("timezone", "America/New_York"),
            filler_enabled=data.get("filler_enabled", True),
        )

        result = orchestrator.generate_from_database(options=options)

        return jsonify({
            "success": True,
            "teams_processed": result.teams_processed,
            "programmes": len(result.programmes),
            "generation_time": (result.completed_at - result.started_at).total_seconds(),
            "xmltv": result.xmltv,
        })

    except Exception as e:
        logger.exception("Team EPG generation failed")
        return jsonify({"success": False, "error": str(e)}), 500


# -----------------------------------------------------------------------------
# Event-based EPG
# -----------------------------------------------------------------------------


@bp.route("/generate/events", methods=["POST"])
def generate_events_epg():
    """Generate event-based EPG for matched streams.

    Request body:
    {
        "matched_streams": [
            {"stream_id": "123", "stream_name": "Lions vs Bears", "channel_id": "ch-1"},
            ...
        ],
        "league": "nfl",
        "date": "2025-12-14",
        "days_ahead": 3,
        "with_filler": true,
        "template": {
            "title_format": "{away_team} @ {home_team}",
            "pregame_enabled": true,
            "postgame_enabled": true
        }
    }

    Returns:
        JSON with XMLTV content, channels, and stats
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400

        league = data.get("league")
        date_str = data.get("date")
        streams_data = data.get("matched_streams", [])

        if not league:
            return jsonify({"success": False, "error": "league required"}), 400

        # Parse date
        if date_str:
            target_date = date.fromisoformat(date_str)
        else:
            target_date = date.today()

        # Create service and matcher
        service = create_default_service()
        matcher = SingleLeagueMatcher(service, league)

        # Match streams to events
        streams = [(s.get("stream_id", ""), s.get("stream_name", "")) for s in streams_data]
        match_result = matcher.match_batch(streams, target_date)

        # Build MatchedStream objects for matched streams
        matched_streams = []
        channel_id_map = {s.get("stream_id"): s.get("channel_id") for s in streams_data}

        for result in match_result.results:
            if result.matched and result.event:
                channel_id = channel_id_map.get(result.stream_id, f"event-{result.event.id}")
                matched_streams.append(MatchedStream(
                    stream_id=result.stream_id,
                    stream_name=result.stream_name,
                    event=result.event,
                    channel_id=channel_id,
                ))

        if not matched_streams:
            return jsonify({
                "success": True,
                "streams_matched": 0,
                "programmes": 0,
                "xmltv": "",
            })

        # Build template config
        template_data = data.get("template", {})
        template = EventTemplateConfig(
            title_format=template_data.get("title_format", "{away_team} @ {home_team}"),
            channel_name_format=template_data.get("channel_name_format", "{away_team_abbrev} @ {home_team_abbrev}"),
            description_format=template_data.get("description_format", "{matchup}"),
            pregame_enabled=template_data.get("pregame_enabled", True),
            postgame_enabled=template_data.get("postgame_enabled", True),
            pregame_title=template_data.get("pregame_title", "Pregame Coverage"),
            postgame_title=template_data.get("postgame_title", "Postgame Recap"),
        )

        options = EventEPGOptions(
            days_ahead=data.get("days_ahead", 14),
            template=template,
            epg_timezone=data.get("timezone", "America/New_York"),
        )

        # Generate EPG
        generator = EventEPGGenerator(service)
        programmes, channels = generator.generate_for_matched_streams(
            matched_streams=matched_streams,
            options=options,
            with_filler=data.get("with_filler", True),
        )

        # Build XMLTV
        channel_dicts = [
            {"id": c.channel_id, "name": c.name, "icon": c.icon}
            for c in channels
        ]
        xmltv = programmes_to_xmltv(programmes, channel_dicts)

        return jsonify({
            "success": True,
            "streams_matched": len(matched_streams),
            "channels": len(channels),
            "programmes": len(programmes),
            "xmltv": xmltv,
            "match_stats": {
                "events_found": match_result.events_found,
                "match_rate": match_result.match_rate,
            }
        })

    except Exception as e:
        logger.exception("Event EPG generation failed")
        return jsonify({"success": False, "error": str(e)}), 500


# -----------------------------------------------------------------------------
# Stream Matching
# -----------------------------------------------------------------------------


@bp.route("/streams/match", methods=["POST"])
def match_streams():
    """Match streams to events using V2 fuzzy matcher.

    Request body:
    {
        "streams": [
            {"id": "123", "name": "Lions vs Bears"},
            {"id": "456", "name": "Lakers @ Celtics"}
        ],
        "league": "nfl",  // or "leagues": ["nfl", "nba"] for multi-league
        "date": "2025-12-14"
    }

    Returns:
        Match results for each stream
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Request body required"}), 400

        streams_data = data.get("streams", [])
        league = data.get("league")
        leagues = data.get("leagues", [])
        date_str = data.get("date")
        exception_keywords = data.get("exception_keywords", [])

        if not streams_data:
            return jsonify({"success": False, "error": "streams required"}), 400

        if not league and not leagues:
            return jsonify({"success": False, "error": "league or leagues required"}), 400

        # Parse date
        if date_str:
            target_date = date.fromisoformat(date_str)
        else:
            target_date = date.today()

        # Create service
        service = create_default_service()

        # Build streams list
        streams = [(s.get("id", ""), s.get("name", "")) for s in streams_data]

        # Match using appropriate matcher
        if leagues:
            matcher = MultiLeagueMatcher(
                service,
                leagues,
                exception_keywords=exception_keywords
            )
            result = matcher.match_all(streams, target_date)
        else:
            matcher = SingleLeagueMatcher(
                service,
                league,
                exception_keywords=exception_keywords
            )
            result = matcher.match_batch(streams, target_date)

        # Format results
        results = []
        for r in result.results:
            results.append({
                "stream_id": r.stream_id,
                "stream_name": r.stream_name,
                "matched": r.matched,
                "match_score": r.match_score,
                "league": r.league,
                "exception_keyword": r.exception_keyword,
                "event": {
                    "id": r.event.id,
                    "name": r.event.name,
                    "start_time": r.event.start_time.isoformat() if r.event.start_time else None,
                    "home_team": r.event.home_team.name if r.event.home_team else None,
                    "away_team": r.event.away_team.name if r.event.away_team else None,
                } if r.event else None,
            })

        return jsonify({
            "success": True,
            "results": results,
            "stats": {
                "events_found": result.events_found,
                "streams_matched": result.streams_matched,
                "streams_total": result.streams_total,
                "match_rate": result.match_rate,
            }
        })

    except Exception as e:
        logger.exception("Stream matching failed")
        return jsonify({"success": False, "error": str(e)}), 500


# -----------------------------------------------------------------------------
# Events Lookup
# -----------------------------------------------------------------------------


@bp.route("/events/<league>/<date_str>", methods=["GET"])
def get_events(league: str, date_str: str):
    """Get events for a league on a specific date.

    Args:
        league: League code (e.g., 'nfl', 'nba', 'eng.1')
        date_str: Date in YYYY-MM-DD format

    Returns:
        List of events
    """
    try:
        target_date = date.fromisoformat(date_str)

        service = create_default_service()
        events = service.get_events(league, target_date)

        results = []
        for e in events:
            results.append({
                "id": e.id,
                "name": e.name,
                "short_name": e.short_name,
                "start_time": e.start_time.isoformat() if e.start_time else None,
                "status": e.status,
                "sport": e.sport,
                "league": e.league,
                "venue": {
                    "name": e.venue.name if e.venue else None,
                    "city": e.venue.city if e.venue else None,
                },
                "home_team": {
                    "id": e.home_team.id,
                    "name": e.home_team.name,
                    "abbreviation": e.home_team.abbreviation,
                    "logo_url": e.home_team.logo_url,
                } if e.home_team else None,
                "away_team": {
                    "id": e.away_team.id,
                    "name": e.away_team.name,
                    "abbreviation": e.away_team.abbreviation,
                    "logo_url": e.away_team.logo_url,
                } if e.away_team else None,
                "home_score": e.home_score,
                "away_score": e.away_score,
            })

        return jsonify({
            "success": True,
            "league": league,
            "date": date_str,
            "count": len(results),
            "events": results,
        })

    except ValueError as e:
        return jsonify({"success": False, "error": f"Invalid date format: {date_str}"}), 400
    except Exception as e:
        logger.exception("Events lookup failed")
        return jsonify({"success": False, "error": str(e)}), 500


# -----------------------------------------------------------------------------
# Health Check
# -----------------------------------------------------------------------------


@bp.route("/health", methods=["GET"])
def health():
    """V2 API health check."""
    return jsonify({
        "status": "ok",
        "version": "2.0",
        "message": "V2 API using pure dataclass pipeline",
    })
