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
from teamarr.consumers.event_epg import is_event_postponed, POSTPONED_LABEL
from teamarr.services import SportsDataService
from teamarr.templates.context import GameContext, TeamChannelContext, TemplateContext
from teamarr.templates.context_builder import ContextBuilder
from teamarr.templates.resolver import TemplateResolver
import logging
from teamarr.utilities.sports import get_sport_duration, get_sport_from_league
from teamarr.utilities.time_blocks import create_filler_chunks, crosses_midnight
from teamarr.utilities.tz import now_user, to_user_tz

from .types import (
    FillerConfig,
    FillerOptions,
    FillerTemplate,
    FillerType,
)

logger = logging.getLogger(__name__)


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
        self._context_builder = ContextBuilder(service)
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
        # Key insight from V1: EPG start should be synchronized with earliest event
        # to avoid gaps between event end and filler start
        now = now_user()
        epg_start = self._calculate_epg_start(sorted_events, now, options)
        epg_end = now + timedelta(days=options.output_days_ahead)

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

        logger.debug(
            "[STARTED] Filler generation for %s: %d events, %d days",
            team_name,
            len(sorted_events),
            (end_date - current_date).days + 1,
        )

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

        logger.debug(
            "[COMPLETED] Filler generation for %s: %d programmes",
            team_name,
            len(fillers),
        )

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

        # Debug logging for idle day .next context
        if not day_events and next_future_event:
            logger.debug(
                f"Idle day {date}: next_future_event={next_future_event.name} on "
                f"{event_date(next_future_event)} ({next_future_event.home_team.name} vs "
                f"{next_future_event.away_team.name})"
            )
        elif not day_events and not next_future_event:
            logger.debug(
                f"Idle day {date}: NO next_future_event found. Total events in schedule: {len(events)}"
            )

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
                    tz=tz,
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
                    tz=tz,
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
        tz,  # ZoneInfo - timezone for midnight crossing detection
    ) -> list[Programme]:
        """Generate fillers for a day with games."""
        fillers: list[Programme] = []

        # Check if previous day's game crosses into today
        skip_pregame_until = day_start
        if prev_day_last_event:
            prev_game_end = self._estimate_event_end(prev_day_last_event).astimezone(tz)
            if prev_game_end > day_start:
                skip_pregame_until = prev_game_end

        # PREGAME: From day start to first game
        # Note: pregame filler ends when the game PROGRAMME starts, not the event
        # The game programme includes a pregame buffer (starts early)
        if config.pregame_enabled:
            first_game = day_events[0]
            pregame_start = skip_pregame_until
            # End filler when game programme starts (event - buffer)
            buffer = timedelta(minutes=options.pregame_buffer_minutes)
            pregame_end_utc = first_game.start_time - buffer
            pregame_end = pregame_end_utc.astimezone(tz)

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
                    next_event=first_game,
                )
                fillers.extend(pregame_progs)

        # POSTGAME: From last game end to midnight (or next game)
        if config.postgame_enabled:
            last_game = day_events[-1]
            postgame_start_utc = self._estimate_event_end(last_game)
            # Convert to local timezone for consistent time block alignment
            postgame_start = postgame_start_utc.astimezone(tz)
            postgame_end = day_end

            # Check if game crosses midnight (in local timezone, not UTC)
            if crosses_midnight(last_game.start_time, postgame_start_utc, tz):
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
        tz=None,  # ZoneInfo - timezone for time alignment
    ) -> list[Programme]:
        """Generate fillers for a day with no games."""
        fillers: list[Programme] = []

        # Check if previous day's game crosses into today
        filler_start = day_start
        if prev_day_last_event:
            prev_game_end_utc = self._estimate_event_end(prev_day_last_event)
            prev_game_end = prev_game_end_utc.astimezone(tz) if tz else prev_game_end_utc
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
                next_event=next_future_event,
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
        next_event: Event | None = None,
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

        # Determine if we should prepend "Postponed: " label
        # - For pregame: check if next_event is postponed
        # - For postgame/idle: check if last_event is postponed
        prepend_label = False
        if self._options and self._options.prepend_postponed_label:
            if filler_type == FillerType.PREGAME and next_event:
                prepend_label = is_event_postponed(next_event)
            elif filler_type in (FillerType.POSTGAME, FillerType.IDLE) and last_event:
                prepend_label = is_event_postponed(last_event)

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

            # Prepend "Postponed: " label if applicable
            if prepend_label:
                title = f"{POSTPONED_LABEL}{title}"
                if subtitle:
                    subtitle = f"{POSTPONED_LABEL}{subtitle}"
                if description:
                    description = f"{POSTPONED_LABEL}{description}"

            # Resolve art URL if present
            # Unknown variables stay literal (e.g., {bad_var}) so user can identify issues
            icon = self._resolver.resolve(template.art_url, context) if template.art_url else None

            # Only include categories if categories_apply_to == "all"
            # Filler never gets xmltv_flags (new/live/date are for live events only)
            # Preserve user's original casing for custom categories
            filler_categories = []
            if config.categories_apply_to == "all":
                # Resolve any {sport} variables in categories
                for cat in config.xmltv_categories:
                    if "{" in cat:
                        filler_categories.append(self._resolver.resolve(cat, context))
                    else:
                        filler_categories.append(cat)

            programme = Programme(
                channel_id=channel_id,
                title=title,
                start=chunk_start,
                stop=chunk_end,
                description=description,
                subtitle=subtitle,
                icon=icon,
                filler_type=filler_type.value,  # 'pregame', 'postgame', or 'idle'
                categories=filler_categories,
                # No xmltv_flags for filler - new/live/date are for live events only
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
                is_final = self._check_event_final(last_event)
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
            # Check for offseason template (no games in schedule_days_ahead lookahead)
            if is_offseason and config.idle_offseason.enabled:
                return FillerTemplate(
                    title=config.idle_offseason.title or config.idle_template.title,
                    subtitle=config.idle_offseason.subtitle or config.idle_template.subtitle,
                    description=config.idle_offseason.description,
                    art_url=config.idle_template.art_url,
                )

            # Check for conditional idle template
            if config.idle_conditional.enabled and last_event:
                is_final = self._check_event_final(last_event)
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

    def _check_event_final(self, event: Event) -> bool:
        """Check if event is final, refreshing status from provider if needed.

        Fetches fresh status via summary endpoint to get accurate final detection.
        """
        if not event:
            return False

        # Refresh event status from provider for accurate final detection
        refreshed = self._service.refresh_event_status(event)

        # Use unified final status check
        from teamarr.utilities.event_status import is_event_final

        return is_event_final(refreshed)

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
            next_game = self._build_game_context(
                next_event, team_config.team_id, team_config.league
            )
            logger.debug(
                f"Built next_game context: opponent={next_game.opponent.name if next_game.opponent else 'None'}, "
                f"event={next_event.name}"
            )

        last_game = None
        if last_event:
            last_game = self._build_game_context(
                last_event, team_config.team_id, team_config.league
            )

        return TemplateContext(
            game_context=None,  # No current game for filler
            team_config=team_config,
            team_stats=team_stats,
            next_game=next_game,
            last_game=last_game,
        )

    def _build_game_context(self, event: Event, team_id: str, league: str) -> GameContext:
        """Build GameContext for a single event.

        Uses ContextBuilder to fetch opponent stats for proper template resolution.
        """
        # Use ContextBuilder which fetches opponent stats with caching
        return self._context_builder._build_game_context(
            event=event,
            team_id=team_id,
            league=league,
        )

    def _estimate_event_end(self, event: Event) -> datetime:
        """Estimate when an event ends based on sport duration."""
        durations = self._options.sport_durations if self._options else {}
        default = self._options.default_duration if self._options else 3.0
        duration_hours = get_sport_duration(event.sport, durations, default)
        return event.start_time + timedelta(hours=duration_hours)

    def _calculate_epg_start(
        self,
        events: list[Event],
        now: datetime,
        options: FillerOptions,
    ) -> datetime:
        """Calculate EPG start time based on lookback_hours.

        The EPG should start from lookback_hours before now, not from
        the earliest event (which could be months ago for team schedules).
        This ensures we only generate filler for the current EPG window.

        Args:
            events: List of events (used for context, not start time)
            now: Current time
            options: Filler options

        Returns:
            EPG start datetime - lookback_hours before now
        """
        # Start from lookback_hours before now (default 6 hours)
        # This matches the team_epg output window calculation
        return now - timedelta(hours=options.lookback_hours)

    def _get_sport(self, league: str) -> str:
        """Derive sport from league identifier (fallback).

        Prefer using event.sport when available (provider is authoritative).
        This is only used when no events are available.
        """
        return get_sport_from_league(league)
