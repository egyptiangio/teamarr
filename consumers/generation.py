"""Full EPG generation using V2 consumers.

This module provides the main entry point for EPG generation,
replacing the V1 generate_all_epg() function with a V2-native
implementation that uses pure dataclass pipeline.

Usage:
    from consumers.generation import generate_epg

    result = generate_epg(settings)
    if result.success:
        print(f"Generated {result.team_stats.programmes} team programmes")
        print(f"Generated {result.event_stats.programmes} event programmes")
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from consumers.orchestrator import Orchestrator, GenerationResult
from consumers.team_epg import TeamEPGOptions
from consumers.event_epg import (
    EventEPGGenerator,
    EventEPGOptions,
    EventTemplateConfig,
    MatchedStream,
)
from consumers.stream_matcher import SingleLeagueMatcher, MultiLeagueMatcher
from core import Programme
from services import create_default_service, SportsDataService
from utilities.xmltv import programmes_to_xmltv
from utilities.tz import now_user, get_user_tz

logger = logging.getLogger(__name__)


@dataclass
class TeamStats:
    """Statistics for team-based EPG generation."""

    count: int = 0
    programmes: int = 0
    events: int = 0
    pregame: int = 0
    postgame: int = 0
    idle: int = 0
    api_calls: int = 0


@dataclass
class EventStats:
    """Statistics for event-based EPG generation."""

    groups_refreshed: int = 0
    streams_matched: int = 0
    programmes: int = 0
    events: int = 0
    pregame: int = 0
    postgame: int = 0
    # Filtering stats
    total_streams: int = 0
    filtered_no_indicator: int = 0
    filtered_exclude_regex: int = 0
    eligible_streams: int = 0


@dataclass
class LifecycleStats:
    """Statistics for channel lifecycle operations."""

    channels_created: int = 0
    channels_updated: int = 0
    channels_deleted: int = 0


@dataclass
class EPGResult:
    """Result of full EPG generation."""

    success: bool
    team_stats: TeamStats = field(default_factory=TeamStats)
    event_stats: EventStats = field(default_factory=EventStats)
    lifecycle_stats: LifecycleStats = field(default_factory=LifecycleStats)
    generation_time: float = 0.0
    error: str | None = None

    # Generated content
    team_xmltv: str = ""
    event_xmltv_files: dict[int, str] = field(default_factory=dict)  # group_id -> xmltv

    def to_dict(self) -> dict:
        """Convert to V1-compatible dict format."""
        return {
            "success": self.success,
            "team_stats": {
                "count": self.team_stats.count,
                "programmes": self.team_stats.programmes,
                "events": self.team_stats.events,
                "pregame": self.team_stats.pregame,
                "postgame": self.team_stats.postgame,
                "idle": self.team_stats.idle,
                "api_calls": self.team_stats.api_calls,
            },
            "event_stats": {
                "groups_refreshed": self.event_stats.groups_refreshed,
                "streams_matched": self.event_stats.streams_matched,
                "programmes": self.event_stats.programmes,
                "events": self.event_stats.events,
                "pregame": self.event_stats.pregame,
                "postgame": self.event_stats.postgame,
                "total_streams": self.event_stats.total_streams,
                "filtered_no_indicator": self.event_stats.filtered_no_indicator,
                "filtered_exclude_regex": self.event_stats.filtered_exclude_regex,
                "eligible_streams": self.event_stats.eligible_streams,
            },
            "lifecycle_stats": {
                "channels_created": self.lifecycle_stats.channels_created,
                "channels_updated": self.lifecycle_stats.channels_updated,
                "channels_deleted": self.lifecycle_stats.channels_deleted,
            },
            "generation_time": self.generation_time,
            "error": self.error,
        }


ProgressCallback = Callable[[str, str, int], None]


def generate_epg(
    settings: dict | None = None,
    progress_callback: ProgressCallback | None = None,
    service: SportsDataService | None = None,
) -> EPGResult:
    """Generate full EPG using V2 consumers.

    This is the main entry point for EPG generation, providing a clean
    interface that uses the V2 dataclass pipeline.

    Args:
        settings: Database settings dict (fetched if None)
        progress_callback: Optional callback(status, message, percent)
        service: Optional SportsDataService (created if None)

    Returns:
        EPGResult with all statistics and generated content
    """
    start_time = datetime.now()

    def report(status: str, message: str, percent: int = 0):
        if progress_callback:
            progress_callback(status, message, percent)

    try:
        # Load settings if not provided
        if settings is None:
            settings = _load_settings()
            if not settings:
                return EPGResult(success=False, error="Settings not configured")

        # Create service
        if service is None:
            service = create_default_service()

        # Get configuration from settings
        days_ahead = settings.get("epg_days_ahead", 14)
        epg_timezone = settings.get("default_timezone", "America/New_York")

        report("starting", "Initializing EPG generation...", 0)

        # Initialize result
        result = EPGResult(success=True)

        # ================================================================
        # PHASE 1: Team-based EPG
        # ================================================================
        report("progress", "Generating team-based EPG...", 10)

        team_result = _generate_team_epg(service, settings)
        result.team_stats = team_result.stats
        result.team_xmltv = team_result.xmltv

        logger.info(
            f"Team EPG: {result.team_stats.programmes} programmes "
            f"from {result.team_stats.count} teams"
        )

        # ================================================================
        # PHASE 2: Event-based EPG
        # ================================================================
        report("progress", "Processing event groups...", 50)

        event_result = _generate_event_epg(service, settings, progress_callback)
        result.event_stats = event_result.stats
        result.event_xmltv_files = event_result.xmltv_files

        logger.info(
            f"Event EPG: {result.event_stats.programmes} programmes "
            f"from {result.event_stats.groups_refreshed} groups"
        )

        # ================================================================
        # PHASE 3: Channel Lifecycle (use existing V1 code)
        # ================================================================
        report("progress", "Processing channel lifecycle...", 85)

        # Channel lifecycle is orthogonal to V1/V2 - keep using existing
        # This would be called here but is handled by app.py

        # ================================================================
        # PHASE 4: Finalize
        # ================================================================
        report("complete", "EPG generation complete!", 100)

        end_time = datetime.now()
        result.generation_time = (end_time - start_time).total_seconds()

        return result

    except Exception as e:
        logger.exception("EPG generation failed")
        return EPGResult(
            success=False,
            error=str(e),
            generation_time=(datetime.now() - start_time).total_seconds(),
        )


@dataclass
class TeamEPGResult:
    """Result of team EPG generation."""

    stats: TeamStats
    xmltv: str
    programmes: list[Programme] = field(default_factory=list)


def _generate_team_epg(
    service: SportsDataService,
    settings: dict,
) -> TeamEPGResult:
    """Generate team-based EPG using V2 orchestrator."""
    try:
        orchestrator = Orchestrator(service)

        # Build options from settings
        sport_durations = {
            "basketball": settings.get("duration_basketball", 3.0),
            "football": settings.get("duration_football", 3.5),
            "hockey": settings.get("duration_hockey", 3.0),
            "baseball": settings.get("duration_baseball", 3.5),
            "soccer": settings.get("duration_soccer", 2.5),
            "mma": settings.get("duration_mma", 5.0),
        }

        options = TeamEPGOptions(
            schedule_days_ahead=settings.get("team_schedule_days_ahead", 30),
            output_days_ahead=settings.get("epg_output_days_ahead", 14),
            pregame_minutes=settings.get("pregame_minutes", 30),
            default_duration_hours=settings.get("duration_default", 3.0),
            filler_enabled=settings.get("filler_enabled", True),
            epg_timezone=settings.get("default_timezone", "America/New_York"),
            sport_durations=sport_durations,
        )

        result = orchestrator.generate_from_database(options=options)

        # Count programme types
        events = pregame = postgame = idle = 0
        for p in result.programmes:
            if "pregame" in (p.title or "").lower():
                pregame += 1
            elif "postgame" in (p.title or "").lower() or "recap" in (p.title or "").lower():
                postgame += 1
            elif "no games" in (p.title or "").lower() or "off day" in (p.title or "").lower():
                idle += 1
            else:
                events += 1

        stats = TeamStats(
            count=result.teams_processed,
            programmes=len(result.programmes),
            events=events,
            pregame=pregame,
            postgame=postgame,
            idle=idle,
            api_calls=result.api_calls,
        )

        return TeamEPGResult(
            stats=stats,
            xmltv=result.xmltv,
            programmes=result.programmes,
        )

    except Exception as e:
        logger.exception("Team EPG generation failed")
        return TeamEPGResult(stats=TeamStats(), xmltv="")


@dataclass
class EventEPGResult:
    """Result of event EPG generation."""

    stats: EventStats
    xmltv_files: dict[int, str]  # group_id -> xmltv content


def _generate_event_epg(
    service: SportsDataService,
    settings: dict,
    progress_callback: ProgressCallback | None = None,
) -> EventEPGResult:
    """Generate event-based EPG for all enabled groups."""
    from database import get_connection, get_all_event_epg_groups

    stats = EventStats()
    xmltv_files = {}

    try:
        # Get enabled event groups
        groups = get_all_event_epg_groups(enabled_only=True)
        groups_with_templates = [
            g for g in groups
            if g.get("event_template_id") or g.get("parent_group_id")
        ]

        if not groups_with_templates:
            return EventEPGResult(stats=stats, xmltv_files={})

        epg_timezone = settings.get("default_timezone", "America/New_York")
        tz = get_user_tz(epg_timezone)
        epg_start = now_user().replace(minute=0, second=0, microsecond=0)

        generator = EventEPGGenerator(service)

        for i, group in enumerate(groups_with_templates):
            group_id = group["id"]
            league = group.get("assigned_league", "")

            if progress_callback:
                pct = 50 + int((i / len(groups_with_templates)) * 35)
                progress_callback(
                    "progress",
                    f"Processing group: {group.get('name', group_id)}",
                    pct,
                )

            try:
                group_result = _process_event_group(
                    group=group,
                    service=service,
                    generator=generator,
                    settings=settings,
                    epg_start=epg_start,
                )

                if group_result:
                    stats.groups_refreshed += 1
                    stats.streams_matched += group_result.streams_matched
                    stats.programmes += group_result.programmes
                    stats.events += group_result.events
                    stats.pregame += group_result.pregame
                    stats.postgame += group_result.postgame
                    stats.total_streams += group_result.total_streams
                    stats.eligible_streams += group_result.eligible_streams

                    if group_result.xmltv:
                        xmltv_files[group_id] = group_result.xmltv

            except Exception as e:
                logger.warning(f"Failed to process group {group_id}: {e}")

        return EventEPGResult(stats=stats, xmltv_files=xmltv_files)

    except Exception as e:
        logger.exception("Event EPG generation failed")
        return EventEPGResult(stats=stats, xmltv_files={})


@dataclass
class GroupResult:
    """Result of processing a single event group."""

    streams_matched: int = 0
    programmes: int = 0
    events: int = 0
    pregame: int = 0
    postgame: int = 0
    total_streams: int = 0
    eligible_streams: int = 0
    xmltv: str = ""


def _process_event_group(
    group: dict,
    service: SportsDataService,
    generator: EventEPGGenerator,
    settings: dict,
    epg_start: datetime,
) -> GroupResult | None:
    """Process a single event group using V2 stream matcher."""
    from database import get_connection

    group_id = group["id"]
    league = group.get("assigned_league", "")
    is_multi_sport = group.get("is_multi_sport", False)

    # Get streams from Dispatcharr (this is infrastructure, not V1/V2)
    streams = _get_group_streams(group)
    if not streams:
        return None

    result = GroupResult(total_streams=len(streams))

    # Build template config from database template
    template = _load_event_template(group.get("event_template_id"))
    template_config = _build_template_config(template)

    options = EventEPGOptions(
        days_ahead=settings.get("epg_days_ahead", 14),
        template=template_config,
        epg_timezone=settings.get("default_timezone", "America/New_York"),
        sport_durations={
            "basketball": settings.get("duration_basketball", 3.0),
            "football": settings.get("duration_football", 3.5),
            "hockey": settings.get("duration_hockey", 3.0),
            "baseball": settings.get("duration_baseball", 3.5),
            "soccer": settings.get("duration_soccer", 2.5),
            "mma": settings.get("duration_mma", 5.0),
        },
    )

    # Match streams to events
    target_date = date.today()
    exception_keywords = _get_exception_keywords(group)

    if is_multi_sport:
        # Multi-sport: use MultiLeagueMatcher
        enabled_leagues = _get_enabled_leagues()
        matcher = MultiLeagueMatcher(
            service,
            enabled_leagues,
            exception_keywords=exception_keywords,
        )
        match_result = matcher.match_all(streams, target_date)
    else:
        # Single league
        matcher = SingleLeagueMatcher(
            service,
            league,
            exception_keywords=exception_keywords,
        )
        match_result = matcher.match_batch(streams, target_date)

    result.eligible_streams = len([r for r in match_result.results if not r.is_exception])

    # Build MatchedStream objects
    matched_streams = []
    for r in match_result.results:
        if r.matched and r.event:
            channel_id = f"teamarr-event-{r.event.id}"
            matched_streams.append(MatchedStream(
                stream_id=r.stream_id,
                stream_name=r.stream_name,
                event=r.event,
                channel_id=channel_id,
            ))

    result.streams_matched = len(matched_streams)

    if not matched_streams:
        return result

    # Generate EPG
    programmes, channels = generator.generate_for_matched_streams(
        matched_streams=matched_streams,
        options=options,
        with_filler=True,
        epg_start=epg_start,
    )

    result.programmes = len(programmes)

    # Count programme types
    for p in programmes:
        title_lower = (p.title or "").lower()
        if "pregame" in title_lower:
            result.pregame += 1
        elif "postgame" in title_lower or "recap" in title_lower:
            result.postgame += 1
        else:
            result.events += 1

    # Generate XMLTV
    channel_dicts = [
        {"id": c.channel_id, "name": c.name, "icon": c.icon}
        for c in channels
    ]
    result.xmltv = programmes_to_xmltv(programmes, channel_dicts)

    return result


def _load_settings() -> dict | None:
    """Load settings from database."""
    from database import get_connection

    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_group_streams(group: dict) -> list[tuple[str, str]]:
    """Get streams for an event group from Dispatcharr.

    Returns list of (stream_id, stream_name) tuples.
    """
    try:
        from api.dispatcharr_client import M3UManager

        settings = _load_settings()
        if not settings or not settings.get("dispatcharr_enabled"):
            return []

        m3u_mgr = M3UManager(
            url=settings["dispatcharr_url"],
            username=settings["dispatcharr_username"],
            password=settings["dispatcharr_password"],
        )

        dispatcharr_group_id = group.get("dispatcharr_group_id")
        if not dispatcharr_group_id:
            return []

        # Get group with streams
        result = m3u_mgr.get_group_with_streams(dispatcharr_group_id)
        if not result:
            return []

        streams = result.get("streams", [])
        return [(str(s.get("id", "")), s.get("name", "")) for s in streams]

    except Exception as e:
        logger.warning(f"Failed to get streams: {e}")
        return []


def _load_event_template(template_id: int | None) -> dict | None:
    """Load event template from database."""
    if not template_id:
        return None

    from database import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM event_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _build_template_config(template: dict | None) -> EventTemplateConfig:
    """Build EventTemplateConfig from database template."""
    if not template:
        return EventTemplateConfig()

    return EventTemplateConfig(
        title_format=template.get("title_format", "{away_team} @ {home_team}"),
        channel_name_format=template.get("channel_name", "{away_team_abbrev} @ {home_team_abbrev}"),
        description_format=template.get("description_template", ""),
        subtitle_format=template.get("subtitle_template", ""),
        category=template.get("category", "Sports"),
        pregame_enabled=bool(template.get("pregame_enabled")),
        pregame_title=template.get("pregame_title", "Pregame Coverage"),
        pregame_description=template.get("pregame_description", ""),
        postgame_enabled=bool(template.get("postgame_enabled")),
        postgame_title=template.get("postgame_title", "Postgame Recap"),
        postgame_description=template.get("postgame_description", ""),
        postgame_conditional_enabled=bool(template.get("postgame_conditional_enabled")),
        postgame_description_final=template.get("postgame_description_final", ""),
        postgame_description_not_final=template.get("postgame_description_not_final", ""),
    )


def _get_exception_keywords(group: dict) -> list[str]:
    """Get exception keywords for a group."""
    keywords = []

    # Group-specific keywords
    if group.get("exception_keywords"):
        try:
            import json
            keywords.extend(json.loads(group["exception_keywords"]))
        except Exception:
            pass

    # Global keywords from settings
    settings = _load_settings()
    if settings and settings.get("consolidation_exception_keywords"):
        try:
            import json
            keywords.extend(json.loads(settings["consolidation_exception_keywords"]))
        except Exception:
            pass

    return keywords


def _get_enabled_leagues() -> list[str]:
    """Get list of enabled leagues from database."""
    from database import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT league_code FROM league_config WHERE enabled = 1"
        ).fetchall()
        return [row["league_code"] for row in rows]
    except Exception:
        # Fallback to common leagues
        return ["nfl", "nba", "nhl", "mlb"]
    finally:
        conn.close()
