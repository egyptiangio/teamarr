"""EPG Orchestrator - coordinates EPG generation.

Supports two modes:
- Team-based: One channel per team, shows team's schedule
- Event-based: One channel per event, shows all events in leagues
"""

from dataclasses import dataclass
from datetime import date, datetime

from teamarr.consumers.event_epg import EventEPGGenerator, EventEPGOptions
from teamarr.consumers.team_epg import TeamEPGGenerator, TeamEPGOptions, TemplateConfig
from teamarr.core import Programme
from teamarr.services import SportsDataService
from teamarr.utilities.xmltv import programmes_to_xmltv


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

        xmltv = programmes_to_xmltv(all_programmes, channels)

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

        programmes, channels = self._event_generator.generate_for_leagues(
            leagues, target_date, channel_prefix, options
        )

        channel_dicts = [
            {"id": ch.channel_id, "name": ch.name, "icon": ch.icon}
            for ch in channels
        ]

        xmltv = programmes_to_xmltv(programmes, channel_dicts)

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
        """Build per-team options, merging config template with base options."""
        # Start with default template
        template = TemplateConfig()

        # Override with config values if set
        if config.title_format:
            template.title_format = config.title_format
        if config.description_format:
            template.description_format = config.description_format
        if config.subtitle_format:
            template.subtitle_format = config.subtitle_format
        if config.category:
            template.category = config.category

        return TeamEPGOptions(
            schedule_days_ahead=base.schedule_days_ahead,
            output_days_ahead=base.output_days_ahead,
            pregame_minutes=base.pregame_minutes,
            default_duration_hours=base.default_duration_hours,
            template=template,
        )

    # Backward compat alias
    generate = generate_for_teams
