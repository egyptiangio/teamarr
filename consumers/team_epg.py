"""Team-based EPG generation - pure dataclass pipeline.

Data flow:
- Service layer returns Event dataclasses (8hr cache for schedule)
- ContextBuilder creates TemplateContext dataclass
- TemplateResolver resolves templates
- Output: Programme dataclasses ready for XMLTV
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from core import Event, Programme, TeamStats
from services import SportsDataService
from template_resolver import TemplateResolver
from template_resolver.context_builder import ContextBuilder
from utilities.sports import get_sport_duration
from utilities.tz import now_user, to_user_tz


@dataclass
class TemplateConfig:
    """Template configuration for EPG generation."""

    title_format: str = "{away_team} @ {home_team}"
    description_format: str = "{matchup} | {venue_full} | {broadcast_simple}"
    subtitle_format: str = "{venue_full}"
    category: str = "Sports"

    # Filler templates
    pregame_title: str = "Pregame Coverage"
    pregame_description: str = "{team_name} vs {opponent.next} starts at {game_time.next}"
    postgame_title: str = "Postgame Recap"
    postgame_description: str = "{team_name} final: {final_score.last}"
    idle_title: str = "{team_name} Programming"
    idle_description: str = "Next game: {game_date.next} vs {opponent.next}"


@dataclass
class TeamEPGOptions:
    """Options for team-based EPG generation."""

    schedule_days_ahead: int = 30  # How far to fetch schedule (for .next vars)
    output_days_ahead: int = 14  # How many days to include in XMLTV
    pregame_minutes: int = 30
    default_duration_hours: float = 3.0
    template: TemplateConfig = field(default_factory=TemplateConfig)

    # Filler generation options
    filler_enabled: bool = True
    epg_timezone: str = "America/New_York"

    # Sport durations (from database settings)
    sport_durations: dict[str, float] = field(default_factory=dict)


@dataclass
class TeamChannelConfig:
    """Team channel configuration (from database)."""

    team_id: str
    league: str
    channel_id: str
    team_name: str
    team_abbrev: str | None = None
    logo_url: str | None = None
    sport: str | None = None

    # Template overrides (from database template)
    title_format: str | None = None
    description_format: str | None = None
    subtitle_format: str | None = None
    category: str | None = None

    # Filler template overrides
    pregame_title: str | None = None
    pregame_description: str | None = None
    postgame_title: str | None = None
    postgame_description: str | None = None
    idle_title: str | None = None
    idle_description: str | None = None


class TeamEPGGenerator:
    """Generates EPG programmes for a team-based channel.

    Pure dataclass pipeline:
    Event → ContextBuilder → TemplateContext → TemplateResolver → Programme
    """

    def __init__(self, service: SportsDataService):
        self._service = service
        self._context_builder = ContextBuilder(service)
        self._resolver = TemplateResolver()

    def generate(
        self,
        config: TeamChannelConfig,
        options: TeamEPGOptions | None = None,
        additional_leagues: list[str] | None = None,
    ) -> list[Programme]:
        """Generate EPG programmes for a team.

        Args:
            config: Team channel configuration
            options: Generation options including templates
            additional_leagues: Extra leagues to fetch schedule from (for multi-league teams)

        Returns:
            List of Programme entries for XMLTV
        """
        options = options or TeamEPGOptions()

        # Build effective template config (merge config overrides with defaults)
        template = self._build_template_config(config, options.template)

        # Collect all leagues to fetch from
        leagues_to_fetch = [config.league]
        if additional_leagues:
            leagues_to_fetch.extend(lg for lg in additional_leagues if lg != config.league)

        # Fetch team schedule from all leagues
        all_events: list[Event] = []
        seen_event_ids: set[str] = set()

        for league in leagues_to_fetch:
            events = self._service.get_team_schedule(
                team_id=config.team_id,
                league=league,
                days_ahead=options.schedule_days_ahead,
            )
            # Dedupe by event ID across leagues
            for event in events:
                if event.id not in seen_event_ids:
                    seen_event_ids.add(event.id)
                    all_events.append(event)

        # Enrich events that need fresh data (today/yesterday)
        all_events = self._enrich_recent_events(all_events)

        # Fetch team stats once for all events
        team_stats = self._service.get_team_stats(config.team_id, config.league)

        # Sort events by time
        sorted_events = sorted(all_events, key=lambda e: e.start_time)

        # Calculate output cutoff
        output_cutoff = now_user() + timedelta(days=options.output_days_ahead)

        programmes: list[Programme] = []

        for i, event in enumerate(sorted_events):
            # Determine next/last events for suffix resolution
            next_event = sorted_events[i + 1] if i + 1 < len(sorted_events) else None
            last_event = sorted_events[i - 1] if i > 0 else None

            # Build template context
            context = self._context_builder.build_for_event(
                event=event,
                team_id=config.team_id,
                league=config.league,
                team_name=config.team_name,
                team_abbrev=config.team_abbrev,
                team_stats=team_stats,
                next_event=next_event,
                last_event=last_event,
                epg_timezone=options.epg_timezone,
            )

            # Only include in output if within output_days_ahead
            if event.start_time > output_cutoff:
                continue

            # Generate programme with template resolution
            programme = self._event_to_programme(
                event=event,
                context=context,
                channel_id=config.channel_id,
                logo_url=config.logo_url,
                template=template,
                options=options,
            )
            if programme:
                programmes.append(programme)

        # Generate filler content if enabled
        if options.filler_enabled and sorted_events:
            filler_programmes = self._generate_fillers(
                events=sorted_events,
                config=config,
                team_stats=team_stats,
                template=template,
                options=options,
            )
            programmes.extend(filler_programmes)

        # Sort all programmes by start time
        programmes.sort(key=lambda p: p.start)

        return programmes

    def _event_to_programme(
        self,
        event: Event,
        context,  # TemplateContext
        channel_id: str,
        logo_url: str | None,
        template: TemplateConfig,
        options: TeamEPGOptions,
    ) -> Programme | None:
        """Convert an Event to a Programme with template resolution."""
        start = event.start_time - timedelta(minutes=options.pregame_minutes)
        duration = get_sport_duration(
            event.sport, options.sport_durations, options.default_duration_hours
        )
        stop = event.start_time + timedelta(hours=duration)

        # Resolve templates
        title = self._resolver.resolve(template.title_format, context)
        description = self._resolver.resolve(template.description_format, context)
        subtitle = self._resolver.resolve(template.subtitle_format, context)

        return Programme(
            channel_id=channel_id,
            title=title,
            start=start,
            stop=stop,
            description=description,
            subtitle=subtitle,
            category=template.category,
            icon=logo_url or (event.home_team.logo_url if event.home_team else None),
        )

    def _enrich_recent_events(self, events: list[Event]) -> list[Event]:
        """Fetch fresh data for recent events (today/yesterday).

        Schedule endpoint is cached for 8hr (discovery only).
        For events that need current status/scores, fetch from
        single event endpoint (30min cache).
        """
        today = now_user().date()
        yesterday = today - timedelta(days=1)

        enriched = []
        for event in events:
            event_date = to_user_tz(event.start_time).date()

            # Only enrich today's and yesterday's events
            if event_date in (today, yesterday):
                fresh = self._service.get_event(event.id, event.league)
                if fresh:
                    enriched.append(fresh)
                else:
                    enriched.append(event)
            else:
                enriched.append(event)

        return enriched

    def _build_template_config(
        self, config: TeamChannelConfig, base: TemplateConfig
    ) -> TemplateConfig:
        """Build effective template config, merging overrides with defaults."""
        return TemplateConfig(
            title_format=config.title_format or base.title_format,
            description_format=config.description_format or base.description_format,
            subtitle_format=config.subtitle_format or base.subtitle_format,
            category=config.category or base.category,
            pregame_title=config.pregame_title or base.pregame_title,
            pregame_description=config.pregame_description or base.pregame_description,
            postgame_title=config.postgame_title or base.postgame_title,
            postgame_description=config.postgame_description or base.postgame_description,
            idle_title=config.idle_title or base.idle_title,
            idle_description=config.idle_description or base.idle_description,
        )

    def _generate_fillers(
        self,
        events: list[Event],
        config: TeamChannelConfig,
        team_stats: TeamStats | None,
        template: TemplateConfig,
        options: TeamEPGOptions,
    ) -> list[Programme]:
        """Generate filler programmes for gaps between events.

        Creates pregame, postgame, and idle content using template variables.
        """
        programmes: list[Programme] = []
        now = now_user()
        output_cutoff = now + timedelta(days=options.output_days_ahead)

        # Filter to events within output window
        output_events = [e for e in events if e.start_time <= output_cutoff]
        if not output_events:
            return programmes

        for i, event in enumerate(output_events):
            next_event = output_events[i + 1] if i + 1 < len(output_events) else None
            last_event = output_events[i - 1] if i > 0 else None

            # Calculate event timing
            duration = get_sport_duration(
                event.sport, options.sport_durations, options.default_duration_hours
            )
            event_start = event.start_time - timedelta(minutes=options.pregame_minutes)
            event_end = event.start_time + timedelta(hours=duration)

            # Build context for filler (game as .next for pregame, as .last for postgame)
            pregame_context = self._context_builder.build_for_filler(
                team_id=config.team_id,
                league=config.league,
                team_name=config.team_name,
                team_abbrev=config.team_abbrev,
                team_logo_url=config.logo_url,
                team_stats=team_stats,
                next_event=event,
                last_event=last_event,
                epg_timezone=options.epg_timezone,
            )

            postgame_context = self._context_builder.build_for_filler(
                team_id=config.team_id,
                league=config.league,
                team_name=config.team_name,
                team_abbrev=config.team_abbrev,
                team_logo_url=config.logo_url,
                team_stats=team_stats,
                next_event=next_event,
                last_event=event,
                epg_timezone=options.epg_timezone,
            )

            # Pregame filler: from end of last event (or start of day) to event start
            if i == 0:
                # First event - pregame from midnight
                pregame_start = datetime.combine(
                    to_user_tz(event.start_time).date(),
                    datetime.min.time(),
                ).replace(tzinfo=event.start_time.tzinfo)
            elif last_event:
                last_duration = get_sport_duration(
                    last_event.sport, options.sport_durations, options.default_duration_hours
                )
                pregame_start = last_event.start_time + timedelta(hours=last_duration)
            else:
                pregame_start = event_start

            if pregame_start < event_start:
                pregame_programme = Programme(
                    channel_id=config.channel_id,
                    title=self._resolver.resolve(template.pregame_title, pregame_context),
                    start=pregame_start,
                    stop=event_start,
                    description=self._resolver.resolve(
                        template.pregame_description, pregame_context
                    ),
                    category=template.category,
                    icon=config.logo_url,
                )
                programmes.append(pregame_programme)

            # Postgame filler: from event end to next event start (or end of day)
            if next_event:
                postgame_end = next_event.start_time - timedelta(
                    minutes=options.pregame_minutes
                )
            else:
                # Last event - postgame to midnight
                postgame_end = datetime.combine(
                    to_user_tz(event.start_time).date() + timedelta(days=1),
                    datetime.min.time(),
                ).replace(tzinfo=event.start_time.tzinfo)

            if event_end < postgame_end:
                postgame_programme = Programme(
                    channel_id=config.channel_id,
                    title=self._resolver.resolve(template.postgame_title, postgame_context),
                    start=event_end,
                    stop=postgame_end,
                    description=self._resolver.resolve(
                        template.postgame_description, postgame_context
                    ),
                    category=template.category,
                    icon=config.logo_url,
                )
                programmes.append(postgame_programme)

        return programmes
