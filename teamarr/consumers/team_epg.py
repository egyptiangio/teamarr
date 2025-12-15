"""Team-based EPG generation.

Takes team configuration, fetches schedule, generates programmes with template support.

Data flow:
- Schedule endpoint (8hr cache): Event discovery (IDs, teams, start times)
- Single event endpoint (30min cache): Fresh status/scores for today's games
"""

from dataclasses import dataclass, field
from datetime import timedelta

from teamarr.core import Event, Programme
from teamarr.services import SportsDataService
from teamarr.templates.context_builder import ContextBuilder
from teamarr.templates.resolver import TemplateResolver
from teamarr.utilities.sports import get_sport_duration
from teamarr.utilities.tz import now_user, to_user_tz


@dataclass
class TemplateConfig:
    """Template configuration for EPG generation."""

    title_format: str = "{away_team} @ {home_team}"
    description_format: str = "{matchup} | {venue_full} | {broadcast_simple}"
    subtitle_format: str = "{venue_full}"
    category: str = "Sports"


@dataclass
class TeamEPGOptions:
    """Options for team-based EPG generation."""

    schedule_days_ahead: int = 30  # How far to fetch schedule (for .next vars)
    output_days_ahead: int = 14    # How many days to include in XMLTV
    pregame_minutes: int = 30
    default_duration_hours: float = 3.0
    template: TemplateConfig = field(default_factory=TemplateConfig)

    # Filler generation options
    filler_enabled: bool = True  # Enable filler generation
    epg_timezone: str = "America/New_York"
    midnight_crossover_mode: str = "postgame"  # 'postgame' or 'idle'

    # Sport durations (from database settings)
    # Keys: basketball, football, hockey, baseball, soccer
    sport_durations: dict[str, float] = field(default_factory=dict)

    # Backwards compatibility
    @property
    def days_ahead(self) -> int:
        return self.output_days_ahead


class TeamEPGGenerator:
    """Generates EPG programmes for a team-based channel.

    Supports multi-league teams (e.g., soccer teams playing in domestic league,
    Champions League, cup competitions, etc.).
    """

    def __init__(self, service: SportsDataService):
        self._service = service
        self._context_builder = ContextBuilder(service)
        self._resolver = TemplateResolver()
        self._filler_generator = None  # Lazy loaded

    def generate_auto_discover(
        self,
        team_id: str,
        primary_league: str,
        channel_id: str,
        team_name: str,
        team_abbrev: str | None = None,
        logo_url: str | None = None,
        options: TeamEPGOptions | None = None,
        provider: str = "espn",
    ) -> list[Programme]:
        """Generate EPG with automatic multi-league discovery.

        Uses the team/league cache to find all leagues the team plays in.

        Args:
            team_id: Provider team ID
            primary_league: Primary league identifier
            channel_id: XMLTV channel ID
            team_name: Display name for the team
            team_abbrev: Team abbreviation
            logo_url: Team/channel logo URL
            options: Generation options
            provider: Data provider ('espn' or 'tsdb')

        Returns:
            List of Programme entries from all discovered leagues
        """
        from teamarr.consumers.team_league_cache import get_cache

        # Look up additional leagues from cache
        cache = get_cache()
        additional_leagues = cache.get_team_leagues(team_id, provider)

        # Remove primary league from additional (will be added back in generate)
        additional_leagues = [lg for lg in additional_leagues if lg != primary_league]

        return self.generate(
            team_id=team_id,
            league=primary_league,
            channel_id=channel_id,
            team_name=team_name,
            team_abbrev=team_abbrev,
            logo_url=logo_url,
            options=options,
            additional_leagues=additional_leagues,
        )

    def generate(
        self,
        team_id: str,
        league: str,
        channel_id: str,
        team_name: str,
        team_abbrev: str | None = None,
        logo_url: str | None = None,
        options: TeamEPGOptions | None = None,
        additional_leagues: list[str] | None = None,
    ) -> list[Programme]:
        """Generate EPG programmes for a team.

        Args:
            team_id: Provider team ID
            league: Primary league identifier (nfl, nba, etc.)
            channel_id: XMLTV channel ID
            team_name: Display name for the team
            team_abbrev: Team abbreviation (e.g., "DET")
            logo_url: Team/channel logo URL
            options: Generation options including templates
            additional_leagues: Extra leagues to fetch schedule from (for multi-league teams)

        Returns:
            List of Programme entries for XMLTV
        """
        options = options or TeamEPGOptions()

        # Collect all leagues to fetch from
        leagues_to_fetch = [league]
        if additional_leagues:
            leagues_to_fetch.extend(lg for lg in additional_leagues if lg != league)

        # Fetch team schedule from all leagues
        all_events: list[Event] = []
        seen_event_ids: set = set()

        for lg in leagues_to_fetch:
            events = self._service.get_team_schedule(
                team_id=team_id,
                league=lg,
                days_ahead=options.schedule_days_ahead,
            )
            # Dedupe by event ID across leagues
            for event in events:
                if event.id not in seen_event_ids:
                    seen_event_ids.add(event.id)
                    all_events.append(event)

        # Enrich events that need fresh data (today/yesterday)
        # Use each event's own league for proper enrichment
        all_events = self._enrich_recent_events(all_events)

        # Fetch team stats once for all events
        team_stats = self._service.get_team_stats(team_id, league)

        # Sort events by time to determine next/last relationships
        sorted_events = sorted(all_events, key=lambda e: e.start_time)

        # Calculate output cutoff date
        output_cutoff = now_user() + timedelta(days=options.output_days_ahead)

        programmes = []
        for i, event in enumerate(sorted_events):
            # Determine next/last events for suffix resolution
            # (uses full schedule for accurate .next vars)
            next_event = sorted_events[i + 1] if i + 1 < len(sorted_events) else None
            last_event = sorted_events[i - 1] if i > 0 else None

            # Build template context (always build for .next/.last vars)
            context = self._context_builder.build_for_event(
                event=event,
                team_id=team_id,
                league=league,
                team_stats=team_stats,
                next_event=next_event,
                last_event=last_event,
            )

            # Only include in output if within output_days_ahead
            if event.start_time > output_cutoff:
                continue

            # Generate programme with template resolution
            programme = self._event_to_programme(
                event=event,
                context=context,
                channel_id=channel_id,
                logo_url=logo_url,
                options=options,
            )
            if programme:
                programmes.append(programme)

        # Generate filler content if enabled
        if options.filler_enabled:
            filler_programmes = self._generate_fillers(
                events=all_events,
                team_id=team_id,
                league=league,
                channel_id=channel_id,
                team_name=team_name,
                team_abbrev=team_abbrev,
                logo_url=logo_url,
                team_stats=team_stats,
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
        options: TeamEPGOptions,
    ) -> Programme | None:
        """Convert an Event to a Programme with template resolution."""
        start = event.start_time - timedelta(minutes=options.pregame_minutes)
        duration = get_sport_duration(
            event.sport, options.sport_durations, options.default_duration_hours
        )
        stop = event.start_time + timedelta(hours=duration)

        # Resolve templates
        title = self._resolver.resolve(options.template.title_format, context)
        description = self._resolver.resolve(options.template.description_format, context)
        subtitle = self._resolver.resolve(options.template.subtitle_format, context)

        return Programme(
            channel_id=channel_id,
            title=title,
            start=start,
            stop=stop,
            description=description,
            subtitle=subtitle,
            category=options.template.category,
            icon=logo_url or event.home_team.logo_url,
        )

    def _enrich_recent_events(self, events: list[Event]) -> list[Event]:
        """Fetch fresh data for recent events (today/yesterday).

        Schedule endpoint is cached for 8hr (discovery only).
        For events that need current status/scores, we fetch from
        single event endpoint (30min cache).

        Uses each event's own league for proper multi-league support.
        """
        today = now_user().date()
        yesterday = today - timedelta(days=1)

        enriched = []
        for event in events:
            # Convert to user timezone before comparing dates
            event_date = to_user_tz(event.start_time).date()

            # Only enrich today's and yesterday's events
            if event_date in (today, yesterday):
                # Use the event's own league for proper enrichment
                fresh = self._service.get_event(event.id, event.league)
                if fresh:
                    enriched.append(fresh)
                else:
                    enriched.append(event)  # Fallback to cached
            else:
                enriched.append(event)

        return enriched

    def _generate_fillers(
        self,
        events: list[Event],
        team_id: str,
        league: str,
        channel_id: str,
        team_name: str,
        team_abbrev: str | None,
        logo_url: str | None,
        team_stats,
        options: TeamEPGOptions,
    ) -> list[Programme]:
        """Generate filler programmes for gaps between events.

        Uses FillerGenerator to create pregame, postgame, and idle content.
        """
        # Lazy import to avoid circular dependency
        from teamarr.consumers.filler import FillerConfig, FillerGenerator, FillerOptions

        # Initialize filler generator if not already done
        if self._filler_generator is None:
            self._filler_generator = FillerGenerator(self._service)

        # Build filler options from EPG options
        filler_options = FillerOptions(
            output_days_ahead=options.output_days_ahead,
            epg_timezone=options.epg_timezone,
            midnight_crossover_mode=options.midnight_crossover_mode,
            sport_durations=options.sport_durations,
            default_duration=options.default_duration_hours,
        )

        # Use default filler config for now
        # TODO: Load from database template when configured
        filler_config = FillerConfig(
            category=options.template.category,
        )

        return self._filler_generator.generate(
            events=events,
            team_id=team_id,
            league=league,
            channel_id=channel_id,
            team_name=team_name,
            team_abbrev=team_abbrev,
            logo_url=logo_url,
            team_stats=team_stats,
            options=filler_options,
            config=filler_config,
        )
