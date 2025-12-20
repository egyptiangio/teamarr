"""Filler generation for team-based EPG.

Generates pregame, postgame, and idle programmes to fill gaps between events.
Filler content aligns to 6-hour time blocks (0000, 0600, 1200, 1800).

Filler types:
- Pregame: Before an event (from day start or previous event to game start)
- Postgame: After an event (from game end to midnight or next event)
- Idle: No games that day (fills the entire day)

Design principles:
- Uses existing TemplateContext/TemplateResolver for consistency
- Supports .next and .last variables for game context
- Respects midnight crossover mode from settings
"""

from datetime import date as date_type
from datetime import datetime, timedelta

from teamarr.core import Event, Programme, TeamStats
from teamarr.services import SportsDataService
from teamarr.templates.context import GameContext, TeamChannelContext, TemplateContext
from teamarr.templates.resolver import TemplateResolver
from teamarr.utilities.sports import get_sport_duration, get_sport_from_league
from teamarr.utilities.time_blocks import create_filler_chunks, crosses_midnight
from teamarr.utilities.tz import now_user, to_user_tz

from .types import (
    FillerConfig,
    FillerOptions,
    FillerTemplate,
    FillerType,
)


class FillerGenerator:
    """Generates filler programmes between events.

    Fills gaps in EPG with pregame, postgame, and idle content.
    Aligns to 6-hour time blocks for clean EPG appearance.

    Usage:
        generator = FillerGenerator(service)
        fillers = generator.generate(
            events=events,
            team_id="8",
            league="nfl",
            channel_id="detroit-lions",
            team_name="Detroit Lions",
            options=FillerOptions(),
            config=FillerConfig(),
        )
    """

    def __init__(self, service: SportsDataService):
        self._service = service
        self._resolver = TemplateResolver()
        self._options: FillerOptions | None = None  # Set during generate()

    def generate(
        self,
        events: list[Event],
        team_id: str,
        league: str,
        channel_id: str,
        team_name: str,
        team_abbrev: str | None = None,
        logo_url: str | None = None,
        team_stats: TeamStats | None = None,
        options: FillerOptions | None = None,
        config: FillerConfig | None = None,
    ) -> list[Programme]:
        """Generate filler programmes for gaps between events.

        Args:
            events: List of events (sorted by start_time)
            team_id: Provider team ID
            league: League identifier
            channel_id: XMLTV channel ID
            team_name: Display name for the team
            team_abbrev: Team abbreviation
            logo_url: Team/channel logo URL
            team_stats: Team statistics (for template resolution)
            options: Generation options
            config: Filler template configuration

        Returns:
            List of filler Programme entries
        """
        options = options or FillerOptions()
        config = config or FillerConfig()
        self._options = options  # Store for use in helper methods

        # Sort events by start time
        sorted_events = sorted(events, key=lambda e: e.start_time)

        # Calculate EPG window
        epg_start = now_user()
        epg_end = epg_start + timedelta(days=options.output_days_ahead)

        # Get sport from events (provider is authoritative), fallback to utility
        sport = sorted_events[0].sport if sorted_events else self._get_sport(league)

        # Build team config for template context
        team_config = TeamChannelContext(
            team_id=team_id,
            league=league,
            sport=sport,
            team_name=team_name,
            team_abbrev=team_abbrev,
        )

        # Generate fillers day by day
        fillers: list[Programme] = []
        current_date = epg_start.date()
        end_date = epg_end.date()

        while current_date <= end_date:
            day_fillers = self._generate_day_fillers(
                date=current_date,
                events=sorted_events,
                team_config=team_config,
                team_stats=team_stats,
                channel_id=channel_id,
                logo_url=logo_url,
                options=options,
                config=config,
                epg_start=epg_start,
            )
            fillers.extend(day_fillers)
            current_date += timedelta(days=1)

        return fillers

    def _generate_day_fillers(
        self,
        date,  # date object
        events: list[Event],
        team_config: TeamChannelContext,
        team_stats: TeamStats | None,
        channel_id: str,
        logo_url: str | None,
        options: FillerOptions,
        config: FillerConfig,
        epg_start: datetime,
    ) -> list[Programme]:
        """Generate fillers for a single day."""
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(options.epg_timezone)

        # Day boundaries
        day_start = datetime.combine(date, datetime.min.time()).replace(tzinfo=tz)
        day_end = datetime.combine(date + timedelta(days=1), datetime.min.time()).replace(tzinfo=tz)

        # On first day, start from epg_start instead of midnight
        if date == epg_start.date():
            day_start = epg_start.replace(second=0, microsecond=0)

        # Helper to get event date in user timezone
        def event_date(e: Event) -> date_type:
            return to_user_tz(e.start_time).date()

        # Get events for this day
        day_events = [e for e in events if event_date(e) == date]

        # Get previous day's last event (for midnight crossover)
        prev_day_events = [e for e in events if event_date(e) == date - timedelta(days=1)]
        prev_day_last_event = prev_day_events[-1] if prev_day_events else None

        # Get next event after this day (for .next context)
        future_events = [e for e in events if event_date(e) > date]
        next_future_event = future_events[0] if future_events else None

        # Find last completed event relative to THIS DAY (for .last context)
        # Important: use day_start (the EPG date) not epg_start (actual now)
        # This ensures .last refers to the most recent game before the programme being generated
        past_events = [e for e in events if e.start_time < day_start]
        last_past_event = past_events[-1] if past_events else None

        fillers: list[Programme] = []

        if day_events:
            # Has games today
            fillers.extend(
                self._generate_game_day_fillers(
                    day_start=day_start,
                    day_end=day_end,
                    day_events=day_events,
                    prev_day_last_event=prev_day_last_event,
                    last_past_event=last_past_event,
                    team_config=team_config,
                    team_stats=team_stats,
                    channel_id=channel_id,
                    logo_url=logo_url,
                    options=options,
                    config=config,
                )
            )
        else:
            # No games today - idle or postgame from previous day
            fillers.extend(
                self._generate_idle_day_fillers(
                    day_start=day_start,
                    day_end=day_end,
                    prev_day_last_event=prev_day_last_event,
                    next_future_event=next_future_event,
                    last_past_event=last_past_event,
                    team_config=team_config,
                    team_stats=team_stats,
                    channel_id=channel_id,
                    logo_url=logo_url,
                    options=options,
                    config=config,
                )
            )

        return fillers

    def _generate_game_day_fillers(
        self,
        day_start: datetime,
        day_end: datetime,
        day_events: list[Event],
        prev_day_last_event: Event | None,
        last_past_event: Event | None,
        team_config: TeamChannelContext,
        team_stats: TeamStats | None,
        channel_id: str,
        logo_url: str | None,
        options: FillerOptions,
        config: FillerConfig,
    ) -> list[Programme]:
        """Generate fillers for a day with games."""
        fillers: list[Programme] = []

        # Check if previous day's game crosses into today
        skip_pregame_until = day_start
        if prev_day_last_event:
            prev_game_end = self._estimate_event_end(prev_day_last_event)
            if prev_game_end > day_start:
                skip_pregame_until = prev_game_end

        # PREGAME: From day start to first game
        # Note: pregame filler ends when the game PROGRAMME starts, not the event
        # The game programme includes a pregame buffer (starts early)
        if config.pregame_enabled:
            first_game = day_events[0]
            pregame_start = skip_pregame_until
            # End filler when game programme starts (event - buffer)
            pregame_end = first_game.start_time - timedelta(minutes=options.pregame_buffer_minutes)

            if pregame_start < pregame_end:
                # Build context for pregame
                context = self._build_filler_context(
                    team_config=team_config,
                    team_stats=team_stats,
                    next_event=first_game,
                    last_event=last_past_event,
                )

                pregame_progs = self._create_filler_programmes(
                    start_dt=pregame_start,
                    end_dt=pregame_end,
                    filler_type=FillerType.PREGAME,
                    context=context,
                    config=config,
                    channel_id=channel_id,
                    logo_url=logo_url,
                )
                fillers.extend(pregame_progs)

        # POSTGAME: From last game end to midnight (or next game)
        if config.postgame_enabled:
            last_game = day_events[-1]
            postgame_start = self._estimate_event_end(last_game)
            postgame_end = day_end

            # Check if game crosses midnight
            if crosses_midnight(last_game.start_time, postgame_start):
                # Game crosses midnight - handled by next day
                pass
            elif postgame_start < postgame_end:
                # Build context for postgame
                # Find next game for .next context
                next_game = None
                for event in day_events:
                    if event.start_time > last_game.start_time:
                        next_game = event
                        break

                context = self._build_filler_context(
                    team_config=team_config,
                    team_stats=team_stats,
                    next_event=next_game,
                    last_event=last_game,
                )

                postgame_progs = self._create_filler_programmes(
                    start_dt=postgame_start,
                    end_dt=postgame_end,
                    filler_type=FillerType.POSTGAME,
                    context=context,
                    config=config,
                    channel_id=channel_id,
                    logo_url=logo_url,
                    last_event=last_game,
                )
                fillers.extend(postgame_progs)

        return fillers

    def _generate_idle_day_fillers(
        self,
        day_start: datetime,
        day_end: datetime,
        prev_day_last_event: Event | None,
        next_future_event: Event | None,
        last_past_event: Event | None,
        team_config: TeamChannelContext,
        team_stats: TeamStats | None,
        channel_id: str,
        logo_url: str | None,
        options: FillerOptions,
        config: FillerConfig,
    ) -> list[Programme]:
        """Generate fillers for a day with no games."""
        fillers: list[Programme] = []

        # Check if previous day's game crosses into today
        filler_start = day_start
        if prev_day_last_event:
            prev_game_end = self._estimate_event_end(prev_day_last_event)
            if prev_game_end > day_start:
                # Previous game crossed midnight
                if options.midnight_crossover_mode == "postgame":
                    # Generate postgame until prev_game_end, then idle
                    if config.postgame_enabled:
                        context = self._build_filler_context(
                            team_config=team_config,
                            team_stats=team_stats,
                            next_event=next_future_event,
                            last_event=prev_day_last_event,
                        )
                        postgame_progs = self._create_filler_programmes(
                            start_dt=day_start,
                            end_dt=min(prev_game_end, day_end),
                            filler_type=FillerType.POSTGAME,
                            context=context,
                            config=config,
                            channel_id=channel_id,
                            logo_url=logo_url,
                            last_event=prev_day_last_event,
                        )
                        fillers.extend(postgame_progs)
                    filler_start = prev_game_end
                else:
                    # Skip until game ends
                    filler_start = prev_game_end

        # IDLE: Rest of the day
        if config.idle_enabled and filler_start < day_end:
            # Determine if offseason (no next game)
            is_offseason = next_future_event is None

            context = self._build_filler_context(
                team_config=team_config,
                team_stats=team_stats,
                next_event=next_future_event,
                last_event=last_past_event or prev_day_last_event,
            )

            idle_progs = self._create_filler_programmes(
                start_dt=filler_start,
                end_dt=day_end,
                filler_type=FillerType.IDLE,
                context=context,
                config=config,
                channel_id=channel_id,
                logo_url=logo_url,
                is_offseason=is_offseason,
                last_event=last_past_event or prev_day_last_event,
            )
            fillers.extend(idle_progs)

        return fillers

    def _create_filler_programmes(
        self,
        start_dt: datetime,
        end_dt: datetime,
        filler_type: FillerType,
        context: TemplateContext,
        config: FillerConfig,
        channel_id: str,
        logo_url: str | None,
        is_offseason: bool = False,
        last_event: Event | None = None,
    ) -> list[Programme]:
        """Create filler programmes aligned to 6-hour time blocks."""
        # Split into time-block-aligned chunks
        chunks = create_filler_chunks(start_dt, end_dt)

        if not chunks:
            return []

        # Get template for this filler type
        template = self._get_filler_template(
            filler_type=filler_type,
            config=config,
            is_offseason=is_offseason,
            last_event=last_event,
        )

        programmes: list[Programme] = []
        for chunk_start, chunk_end in chunks:
            # Resolve templates
            title = self._resolver.resolve(template.title, context)
            description = ""
            if template.description:
                description = self._resolver.resolve(template.description, context)
            subtitle = None
            if template.subtitle:
                subtitle = self._resolver.resolve(template.subtitle, context)

            programme = Programme(
                channel_id=channel_id,
                title=title,
                start=chunk_start,
                stop=chunk_end,
                description=description,
                subtitle=subtitle,
                category=config.category,
                icon=logo_url,
            )
            programmes.append(programme)

        return programmes

    def _get_filler_template(
        self,
        filler_type: FillerType,
        config: FillerConfig,
        is_offseason: bool = False,
        last_event: Event | None = None,
    ) -> FillerTemplate:
        """Get appropriate template based on filler type and conditions."""
        if filler_type == FillerType.PREGAME:
            return config.pregame_template

        elif filler_type == FillerType.POSTGAME:
            # Check for conditional postgame template
            if config.postgame_conditional.enabled and last_event:
                is_final = last_event.status.state == "final"
                if is_final and config.postgame_conditional.description_final:
                    return FillerTemplate(
                        title=config.postgame_template.title,
                        subtitle=config.postgame_template.subtitle,
                        description=config.postgame_conditional.description_final,
                        art_url=config.postgame_template.art_url,
                    )
                elif not is_final and config.postgame_conditional.description_not_final:
                    return FillerTemplate(
                        title=config.postgame_template.title,
                        subtitle=config.postgame_template.subtitle,
                        description=config.postgame_conditional.description_not_final,
                        art_url=config.postgame_template.art_url,
                    )
            return config.postgame_template

        else:  # IDLE
            # Check for offseason template
            if is_offseason and config.idle_offseason.enabled:
                return FillerTemplate(
                    title=config.idle_offseason.title or config.idle_template.title,
                    subtitle=config.idle_offseason.subtitle,
                    description=config.idle_offseason.description,
                    art_url=config.idle_template.art_url,
                )

            # Check for conditional idle template
            if config.idle_conditional.enabled and last_event:
                is_final = last_event.status.state == "final"
                if is_final and config.idle_conditional.description_final:
                    return FillerTemplate(
                        title=config.idle_template.title,
                        subtitle=config.idle_template.subtitle,
                        description=config.idle_conditional.description_final,
                        art_url=config.idle_template.art_url,
                    )
                elif not is_final and config.idle_conditional.description_not_final:
                    return FillerTemplate(
                        title=config.idle_template.title,
                        subtitle=config.idle_template.subtitle,
                        description=config.idle_conditional.description_not_final,
                        art_url=config.idle_template.art_url,
                    )

            return config.idle_template

    def _build_filler_context(
        self,
        team_config: TeamChannelContext,
        team_stats: TeamStats | None,
        next_event: Event | None = None,
        last_event: Event | None = None,
    ) -> TemplateContext:
        """Build template context for filler content.

        For filler, game_context is None (no current game).
        .next and .last contexts are populated from next/last events.
        """
        next_game = None
        if next_event:
            next_game = self._build_game_context(next_event, team_config.team_id)

        last_game = None
        if last_event:
            last_game = self._build_game_context(last_event, team_config.team_id)

        return TemplateContext(
            game_context=None,  # No current game for filler
            team_config=team_config,
            team_stats=team_stats,
            next_game=next_game,
            last_game=last_game,
        )

    def _build_game_context(self, event: Event, team_id: str) -> GameContext:
        """Build GameContext for a single event."""
        # Determine home/away from event
        is_home = event.home_team.id == team_id
        team = event.home_team if is_home else event.away_team
        opponent = event.away_team if is_home else event.home_team

        return GameContext(
            event=event,
            is_home=is_home,
            team=team,
            opponent=opponent,
            opponent_stats=None,  # Could be populated if needed
        )

    def _estimate_event_end(self, event: Event) -> datetime:
        """Estimate when an event ends based on sport duration."""
        durations = self._options.sport_durations if self._options else {}
        default = self._options.default_duration if self._options else 3.0
        duration_hours = get_sport_duration(event.sport, durations, default)
        return event.start_time + timedelta(hours=duration_hours)

    def _get_sport(self, league: str) -> str:
        """Derive sport from league identifier (fallback).

        Prefer using event.sport when available (provider is authoritative).
        This is only used when no events are available.
        """
        return get_sport_from_league(league)
