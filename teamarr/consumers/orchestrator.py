"""EPG Orchestrator - coordinates EPG generation.

Supports two modes:
- Team-based: One channel per team, shows team's schedule
- Event-based: One channel per event, shows all events in leagues
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from teamarr.consumers.event_epg import EventEPGGenerator, EventEPGOptions
from teamarr.consumers.team_epg import TeamEPGGenerator, TeamEPGOptions
from teamarr.core import Programme, TemplateConfig
from teamarr.services import SportsDataService
from teamarr.utilities.xmltv import programmes_to_xmltv

logger = logging.getLogger(__name__)


@dataclass
class TeamChannelConfig:
    """Team channel configuration for orchestrator."""

    team_id: str
    league: str
    channel_id: str
    team_name: str
    team_abbrev: str | None = None
    logo_url: str | None = None
    # Template config (optional - uses defaults if not set)
    title_format: str | None = None
    description_format: str | None = None
    subtitle_format: str | None = None
    category: str | None = None
    # Database template ID for filler config
    template_id: int | None = None
    # Additional leagues to check for events (e.g., Champions League for soccer)
    additional_leagues: list[str] = field(default_factory=list)


@dataclass
class GenerationResult:
    """Result of an EPG generation run."""

    programmes: list[Programme]
    xmltv: str
    teams_processed: int
    events_processed: int
    started_at: datetime
    completed_at: datetime


class Orchestrator:
    """Coordinates EPG generation workflow."""

    def __init__(self, service: SportsDataService):
        self._service = service
        self._team_generator = TeamEPGGenerator(service)
        self._event_generator = EventEPGGenerator(service)

    def generate_for_teams(
        self,
        team_configs: list[TeamChannelConfig],
        options: TeamEPGOptions | None = None,
    ) -> GenerationResult:
        """Generate EPG for teams (team-based mode)."""
        started_at = datetime.now()
        base_options = options or TeamEPGOptions()

        logger.debug(
            "[STARTED] Team EPG generation: %d teams, %d days ahead",
            len(team_configs),
            base_options.output_days_ahead,
        )

        all_programmes: list[Programme] = []

        for config in team_configs:
            # Build per-team options with template from config
            team_options = self._build_team_options(config, base_options)

            programmes = self._team_generator.generate(
                team_id=config.team_id,
                league=config.league,
                channel_id=config.channel_id,
                team_name=config.team_name,
                team_abbrev=config.team_abbrev,
                logo_url=config.logo_url,
                options=team_options,
            )
            all_programmes.extend(programmes)

        channels = [
            {
                "id": config.channel_id,
                "name": config.team_name,
                "icon": config.logo_url,
            }
            for config in team_configs
        ]

        xmltv = programmes_to_xmltv(
            all_programmes,
            channels,
            generator_name=options.generator_name
            if options and options.generator_name
            else "Teamarr",
            generator_url=options.generator_url if options else None,
        )

        duration_ms = int((datetime.now() - started_at).total_seconds() * 1000)
        logger.info(
            "[COMPLETED] Team EPG generation: %d teams, %d programmes, %d bytes, %dms",
            len(team_configs),
            len(all_programmes),
            len(xmltv),
            duration_ms,
        )

        return GenerationResult(
            programmes=all_programmes,
            xmltv=xmltv,
            teams_processed=len(team_configs),
            events_processed=0,
            started_at=started_at,
            completed_at=datetime.now(),
        )

    def generate_for_events(
        self,
        leagues: list[str],
        target_date: date,
        channel_prefix: str = "event",
        options: EventEPGOptions | None = None,
    ) -> GenerationResult:
        """Generate EPG for events (event-based mode).

        Fetches all events from specified leagues and generates
        a channel/programme for each.
        """
        started_at = datetime.now()
        options = options or EventEPGOptions()

        logger.debug(
            "[STARTED] Event EPG generation: %d leagues, date=%s",
            len(leagues),
            target_date,
        )

        programmes, channels = self._event_generator.generate_for_leagues(
            leagues, target_date, channel_prefix, options
        )

        channel_dicts = [{"id": ch.channel_id, "name": ch.name, "icon": ch.icon} for ch in channels]

        xmltv = programmes_to_xmltv(
            programmes,
            channel_dicts,
            generator_name=options.generator_name if options.generator_name else "Teamarr",
            generator_url=options.generator_url,
        )

        duration_ms = int((datetime.now() - started_at).total_seconds() * 1000)
        logger.info(
            "[COMPLETED] Event EPG generation: %d leagues, %d events, %d bytes, %dms",
            len(leagues),
            len(programmes),
            len(xmltv),
            duration_ms,
        )

        return GenerationResult(
            programmes=programmes,
            xmltv=xmltv,
            teams_processed=0,
            events_processed=len(programmes),
            started_at=started_at,
            completed_at=datetime.now(),
        )

    def _build_team_options(
        self, config: TeamChannelConfig, base: TeamEPGOptions
    ) -> TeamEPGOptions:
        """Build per-team options, merging config template with base options.

        Template is loaded from database via template_id. Direct template format
        values (title_format, etc.) are only used if ALL values are provided.
        Otherwise, template_id must be set for proper template loading.
        """
        # Build template only if ALL format values are provided
        # Otherwise, rely on template_id for database loading
        template = None
        if all([config.title_format, config.description_format, config.subtitle_format]):
            template = TemplateConfig(
                title_format=config.title_format,
                description_format=config.description_format,
                subtitle_format=config.subtitle_format,
                category=config.category or "Sports",
                conditional_descriptions=[],  # Direct API calls don't use conditionals
            )

        return TeamEPGOptions(
            schedule_days_ahead=base.schedule_days_ahead,
            output_days_ahead=base.output_days_ahead,
            pregame_minutes=base.pregame_minutes,
            default_duration_hours=base.default_duration_hours,
            template=template,
            sport_durations=base.sport_durations,
            filler_enabled=base.filler_enabled,
            epg_timezone=base.epg_timezone,
            midnight_crossover_mode=base.midnight_crossover_mode,
            template_id=config.template_id,
        )

    # Backward compat alias
    generate = generate_for_teams
